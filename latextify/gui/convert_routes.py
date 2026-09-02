"""The two manuscript-conversion POST routes, extracted from ``server.py``.

    POST /api/convert         one .docx -> one journal project (+ optional PDF)
    POST /api/convert-multi   a manuscript plus its supplement, figure
                              overrides, and reference-manager export, with
                              per-document layout options

Together these were 467 of ``server.py``'s 909 lines -- it was pinned at 921
by the size ratchet and had spent years accreting here because the routes are
closures over ``create_app``'s scope. They need exactly three values from it
(``root``, ``max_upload_bytes``, ``demo``), so they take those as arguments
instead and ``server.py`` drops under the general ceiling entirely.

:func:`register_convert_routes` attaches both to an app, mirroring
:func:`latextify.gui.uploads_routes.register_upload_routes` and
:func:`latextify.gui.downloads.register_download_routes`. Both carry the same
guard as every other mutating ``/api/*`` endpoint (``require_gui_auth`` +
``require_demo_rate_limit``; see ``server.py``'s module docstring Security
section), and both hand back a download token rather than a file -- session
bookkeeping lives in :mod:`latextify.gui.downloads`.
"""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile

from latextify.audit.equations import write_equation_audit
from latextify.compile.pdf import staple_pdfs
from latextify.compile.tectonic import compile_document, ensure_tectonic
from latextify.emit.project import emit_project
from latextify.gui.convert_inputs import stage_multi_uploads, validate_multi_form
from latextify.gui.demo import require_demo_rate_limit
from latextify.gui.downloads import _issue_token, _register_session, _rmtree
from latextify.gui.exporting import _export_artifacts, default_export_roots
from latextify.gui.guard import require_gui_auth
from latextify.gui.schemas import ConvertMultiResponse, ConvertResponse
from latextify.gui.upload_utils import (
    _safe_filename,
    _stream_upload,
)
from latextify.gui.validation_view import build_validation_out
from latextify.report.render import write_report
from latextify.templates import loader as templates_loader
from latextify.templates.loader import ManifestError


def register_convert_routes(
    app: FastAPI,
    *,
    root: Path,
    max_upload_bytes: int,
    demo: bool,
    export_roots: list[Path] | None = None,
) -> None:
    """Attach ``/api/convert`` and ``/api/convert-multi`` to ``app``.

    Args:
        root: parent directory each run's per-session working directory is
            created under.
        max_upload_bytes: per-file cap enforced while streaming an upload
            (demo-mode-lowered or the default, chosen by the caller).
        demo: hosted-demo mode, which refuses the server-filesystem export
            path -- a hosted instance must not write to its own disk on
            request.
        export_roots: folders the optional export step may write under
            (default: :func:`latextify.gui.exporting.default_export_roots`).
    """
    export_roots = list(export_roots) if export_roots is not None else default_export_roots()

    @app.post(
        "/api/convert",
        response_model=ConvertResponse,
        dependencies=[Depends(require_gui_auth), Depends(require_demo_rate_limit)],
    )
    async def convert(
        file: UploadFile = File(...),
        journal: str = Form(...),
        citation_style: str | None = Form(None),
        pdf: bool = Form(False),
    ) -> ConvertResponse:
        try:
            journal_obj = templates_loader.load(journal)
        except ManifestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        session_dir = root / uuid.uuid4().hex
        upload_dir = session_dir / "upload"
        upload_dir.mkdir(parents=True, exist_ok=True)
        docx_path = upload_dir / _safe_filename(file.filename)

        try:
            await _stream_upload(file, docx_path, max_bytes=max_upload_bytes)
            result = emit_project(
                docx_path,
                journal,
                session_dir / "output",
                citation_style=citation_style,
            )
        except ValueError as exc:
            # Every ingest-boundary module raises a clean ValueError naming
            # the problem for a corrupt/unsupported .docx or an unsupported
            # citation style (ManifestError is itself a ValueError subclass)
            # -- see latextify.cli's `convert` command for the identical
            # contract. Never let one surface as a raw 500 traceback.
            _rmtree(session_dir)  # a failed run must not leave the upload behind
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception:
            _rmtree(session_dir)
            raise

        warnings = [w.message for w in result.warnings]
        pdf_url: str | None = None
        success = True

        if pdf:
            try:
                vendor_dir = journal_obj.root / "vendor" if journal_obj.vendor else None
                compile_result = compile_document(
                    result.main_tex_path,
                    tectonic_path=ensure_tectonic(),
                    vendor_dir=vendor_dir,
                )
            except Exception as exc:
                # Mirrors the CLI's `except Exception` around --pdf: a hung
                # compile raises subprocess.TimeoutExpired, a present-but-
                # broken tectonic binary raises OSError. Never a raw 500
                # traceback for either.
                _rmtree(session_dir)
                raise HTTPException(status_code=500, detail=f"compilation failed: {exc}") from exc

            success = compile_result.success
            if result.report_path is not None:
                write_report(
                    result.report_path,
                    preflight=None,
                    emit_result=result,
                    reconciliation=None,
                    compile_result=compile_result,
                )
            if compile_result.success and compile_result.pdf_path is not None:
                token = uuid.uuid4().hex
                app.state.pdf_tokens[token] = compile_result.pdf_path
                pdf_url = f"/api/pdf/{token}"

        report_md = ""
        if result.report_path is not None and result.report_path.is_file():
            report_md = result.report_path.read_text(encoding="utf-8")

        # Bound this run's footprint under the same TTL/LRU pruning as
        # convert-multi (audit item 3); the single-file endpoint otherwise
        # leaked its session dir + pdf token forever (tech-debt finding 3).
        _register_session(
            app,
            uuid.uuid4().hex,
            {"output_dir": result.output_dir, "produced": {}},
            session_dir=session_dir,
            now=time.time(),
        )

        return ConvertResponse(
            output_dir=str(result.output_dir),
            warnings=warnings,
            report_md=report_md,
            success=success,
            pdf_url=pdf_url,
        )

    @app.post(
        "/api/convert-multi",
        response_model=ConvertMultiResponse,
        dependencies=[Depends(require_gui_auth), Depends(require_demo_rate_limit)],
    )
    async def convert_multi(
        main: UploadFile = File(...),
        journal: str = Form(...),
        supplement: UploadFile | None = File(None),
        figures: list[UploadFile] = File([]),
        figure_numbers: list[int] = Form([]),
        references: UploadFile | None = File(None),
        citation_style: str | None = Form(None),
        crossref_mailto: str | None = Form(None),
        combine: bool = Form(False),
        supplement_onecolumn: bool = Form(False),
        exclude_figures: bool = Form(False),
        equation_audit: bool = Form(False),
        check_references: bool = Form(False),
        want_zip: bool = Form(False),
        pdf: bool = Form(True),
        export_dir: str | None = Form(None),
        export_types: list[str] = Form([]),
        main_columns: str = Form("default"),
        main_line_numbers: bool = Form(False),
        main_double_spacing: bool = Form(False),
        supplement_columns: str = Form("default"),
        supplement_line_numbers: bool = Form(False),
        supplement_double_spacing: bool = Form(False),
        anonymize: bool = Form(False),
        figures_at_end: bool = Form(False),
    ) -> ConvertMultiResponse:
        """Convert a main manuscript plus optional supplement/figures/.bib in one call.

        Figures are dropped in as ``figures/fig<N>.<ext>`` beside the main docx
        (folder-convention override); the ``.bib`` seeds offline citation
        matching; ``combine`` staples main+supplement into ``combined.pdf``;
        ``equation_audit`` emits a numbered ``audit.pdf``; ``want_zip`` packages
        the whole project tree. Every produced artifact is returned as an opaque
        download token.
        """
        main_layout, supplement_layout = validate_multi_form(
            main=main,
            supplement=supplement,
            references=references,
            figures=figures,
            figure_numbers=figure_numbers,
            combine=combine,
            pdf=pdf,
            demo=demo,
            export_dir=export_dir,
            main_columns=main_columns,
            main_line_numbers=main_line_numbers,
            main_double_spacing=main_double_spacing,
            supplement_columns=supplement_columns,
            supplement_line_numbers=supplement_line_numbers,
            supplement_double_spacing=supplement_double_spacing,
        )

        try:
            journal_obj = templates_loader.load(journal)
        except ManifestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        staged = await stage_multi_uploads(
            root=root,
            max_upload_bytes=max_upload_bytes,
            main=main,
            supplement=supplement,
            references=references,
            figures=figures,
            figure_numbers=figure_numbers,
        )
        session_dir = staged.session_dir
        main_path = staged.main_path
        supplement_path = staged.supplement_path
        references_path = staged.references_path

        try:
            result = emit_project(
                main_path,
                journal,
                session_dir / "output",
                citation_style=citation_style,
                crossref_mailto=crossref_mailto,
                supplement_docx_path=supplement_path,
                references_bib_path=references_path,
                supplement_onecolumn=supplement_onecolumn,
                exclude_figures=exclude_figures,
                check_references=check_references,
                main_layout=main_layout,
                supplement_layout=supplement_layout,
                anonymize=anonymize,
                figures_at_end=figures_at_end,
            )
        except ValueError as exc:
            _rmtree(session_dir)  # a failed emit leaves the upload behind otherwise
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        warnings = [w.message for w in result.warnings]
        if result.supplement is not None:
            warnings.extend(w.message for w in result.supplement.warnings)

        pdf_url: str | None = None
        supplement_pdf_url: str | None = None
        combined_pdf_url: str | None = None
        audit_pdf_url: str | None = None
        zip_url: str | None = None
        success = True
        main_compile_success: bool | None = None
        supplement_compile_success: bool | None = None
        # Real paths of every produced artifact, for the optional folder export.
        produced: dict[str, Path] = {"project": result.output_dir}

        pdf_tokens = app.state.pdf_tokens
        if pdf:
            try:
                tectonic = ensure_tectonic()
                vendor_dir = journal_obj.root / "vendor" if journal_obj.vendor else None
                main_compile = compile_document(
                    result.main_tex_path, tectonic_path=tectonic, vendor_dir=vendor_dir
                )
                main_compile_success = main_compile.success
                if main_compile.success and main_compile.pdf_path is not None:
                    pdf_url = f"/api/pdf/{_issue_token(pdf_tokens, main_compile.pdf_path)}"
                    produced["main_pdf"] = main_compile.pdf_path

                supplement_compile = None
                if result.supplement is not None:
                    supplement_compile = compile_document(
                        result.supplement.supplement_tex_path,
                        tectonic_path=tectonic,
                        vendor_dir=vendor_dir,
                    )
                    supplement_compile_success = supplement_compile.success
                    if supplement_compile.success and supplement_compile.pdf_path is not None:
                        supplement_pdf_url = (
                            f"/api/pdf/{_issue_token(pdf_tokens, supplement_compile.pdf_path)}"
                        )
                        produced["supplement_pdf"] = supplement_compile.pdf_path
                    else:
                        warnings.append(
                            "supplement PDF failed to compile -- the main document is unaffected; "
                            "see the supplement diagnostics in report.md."
                        )

                # Overall success requires EVERY requested compile to succeed, so a
                # main-ok/supplement-failed run is honestly reported as not-success.
                success = main_compile.success and (
                    supplement_compile is None or supplement_compile.success
                )

                if (
                    combine
                    and main_compile.success
                    and supplement_compile is not None
                    and supplement_compile.success
                ):
                    combined = result.output_dir / "combined.pdf"
                    staple_pdfs([main_compile.pdf_path, supplement_compile.pdf_path], combined)
                    combined_pdf_url = f"/api/pdf/{_issue_token(pdf_tokens, combined)}"
                    produced["combined_pdf"] = combined

                if result.report_path is not None:
                    write_report(
                        result.report_path,
                        preflight=None,
                        emit_result=result,
                        reconciliation=None,
                        compile_result=main_compile,
                        supplement=result.supplement,
                        supplement_compile=supplement_compile,
                        validation=result.validation,
                    )
            except HTTPException:
                _rmtree(session_dir)
                raise
            except Exception as exc:
                # Mirrors /api/convert: a hung/broken compile is a 500, not a raw
                # traceback. The LaTeX project itself is still written to disk.
                _rmtree(session_dir)
                raise HTTPException(status_code=500, detail=f"compilation failed: {exc}") from exc

        if equation_audit:
            try:
                audit = write_equation_audit(
                    main_path,
                    session_dir / "audit",
                    compile_pdf=pdf,
                    tectonic_path=ensure_tectonic() if pdf else None,
                )
                if audit.audit_pdf_path is not None and audit.audit_pdf_path.is_file():
                    audit_pdf_url = f"/api/pdf/{_issue_token(pdf_tokens, audit.audit_pdf_path)}"
                    produced["audit_pdf"] = audit.audit_pdf_path
            except Exception as exc:
                _rmtree(session_dir)
                raise HTTPException(
                    status_code=500, detail=f"equation audit failed: {exc}"
                ) from exc

        if want_zip:
            archive = shutil.make_archive(
                str(session_dir / "project"), "zip", root_dir=result.output_dir
            )
            produced["zip"] = Path(archive)
            zip_url = f"/api/zip/{_issue_token(app.state.zip_tokens, Path(archive))}"

        # Optional export: copy the selected artifact types to a chosen folder on
        # the user's machine (this is a localhost tool; the folder came from the
        # native picker or manual entry). Never fatal to a successful conversion.
        exported_to: str | None = None
        exported: list[str] = []
        if export_dir and export_dir.strip():
            try:
                exported_to, exported, export_warnings = _export_artifacts(
                    export_dir.strip(),
                    set(export_types),
                    output_dir=result.output_dir,
                    produced=produced,
                    roots=export_roots,
                )
                warnings.extend(export_warnings)
            except (OSError, ValueError) as exc:
                _rmtree(session_dir)
                raise HTTPException(
                    status_code=400, detail=f"could not export to {export_dir!r}: {exc}"
                ) from exc

        # Register this run's artifacts so the UI can export them later without
        # recompiling (preview-then-export). Also carry the entry set + validation
        # + compile context so /api/apply-corrections can rewrite references.bib
        # and recompile the SAME project without a re-conversion. The session
        # (and its on-disk directory) is TTL-bounded + LRU-capped + shutdown-swept
        # by _register_session / _prune_sessions / the lifespan (audit item 3).
        export_token = uuid.uuid4().hex
        _register_session(
            app,
            export_token,
            {
                "output_dir": result.output_dir,
                "produced": produced,
                "entries": result.entries,
                "validation": result.validation,
                "bib_path": result.bib_path,
                "main_tex_path": result.main_tex_path,
                "supplement_tex_path": (
                    result.supplement.supplement_tex_path if result.supplement else None
                ),
                "journal": journal,
                "compiled": pdf,
                "combine": combine,
            },
            session_dir=session_dir,
            now=time.time(),
        )

        report_md = ""
        if result.report_path is not None and result.report_path.is_file():
            report_md = result.report_path.read_text(encoding="utf-8")

        validation_out = (
            build_validation_out(result.validation, result.entries)
            if result.validation is not None
            else None
        )

        return ConvertMultiResponse(
            output_dir=str(result.output_dir),
            warnings=warnings,
            report_md=report_md,
            success=success,
            main_compile_success=main_compile_success,
            supplement_compile_success=supplement_compile_success,
            pdf_url=pdf_url,
            supplement_pdf_url=supplement_pdf_url,
            combined_pdf_url=combined_pdf_url,
            audit_pdf_url=audit_pdf_url,
            zip_url=zip_url,
            exported_to=exported_to,
            exported=exported,
            export_token=export_token,
            validation=validation_out,
        )
