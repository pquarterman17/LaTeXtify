"""Everything between "a multipart form arrived" and "there are files to convert".

Split out of :mod:`latextify.gui.convert_routes` (2026-08-10), which was itself
split out of ``server.py``. ``/api/convert-multi`` accepts 24 form fields, and
the work of checking them and getting their uploads onto disk was roughly a
third of the route -- with no HTTP logic in it beyond raising the 400s.

Two functions, in the order the route calls them:

    validate_multi_form   every rejection, BEFORE anything is written or pandoc
                          runs, so an invalid request never pays for a
                          conversion it will not receive
    stage_multi_uploads   stream the uploads into a fresh session directory,
                          cleaning it up if any of them fails

The extension checks here are a fast first gate, not a replacement for content
validation: a file can carry ``.docx`` and be a corrupt ZIP, which
``emit_project`` catches downstream and reports as its own 400.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, UploadFile

from latextify.emit.submission import DocumentLayout, parse_layout_form
from latextify.gui.downloads import _rmtree
from latextify.gui.upload_utils import (
    _ALLOWED_FIGURE_EXTS,
    _ALLOWED_MANUSCRIPT_EXTS,
    _ALLOWED_REFERENCE_EXTS,
    _lower_ext,
    _stream_upload,
)

#: Demo mode refuses the server-filesystem export path: a hosted instance must
#: not write to a caller-chosen path on a shared host.
_DEMO_FS_DISABLED = (
    "folder export is disabled in the hosted demo -- download the PDF or the project .zip instead"
)


@dataclass(frozen=True)
class StagedUploads:
    """Where :func:`stage_multi_uploads` put one request's files.

    ``session_dir`` owns everything: the caller registers it for TTL/LRU
    pruning on success, and it is already removed if staging raised.
    """

    session_dir: Path
    main_path: Path
    supplement_path: Path | None
    references_path: Path | None


def validate_multi_form(
    *,
    main: UploadFile,
    supplement: UploadFile | None,
    references: UploadFile | None,
    figures: list[UploadFile],
    figure_numbers: list[int],
    combine: bool,
    pdf: bool,
    demo: bool,
    export_dir: str | None,
    main_columns: str,
    main_line_numbers: bool,
    main_double_spacing: bool,
    supplement_columns: str,
    supplement_line_numbers: bool,
    supplement_double_spacing: bool,
) -> tuple[DocumentLayout, DocumentLayout]:
    """Reject every bad request shape; return the parsed (main, supplement) layouts.

    Raises :class:`fastapi.HTTPException` with a 400 (or 403 for the demo
    filesystem refusal) naming the offending field. Nothing here touches disk,
    which is the point: the expensive work never starts for a request that
    cannot succeed.
    """
    # Per-document layout overrides (plan item 6); a bad columns value is a
    # clean 400 naming the field, before anything touches disk.
    try:
        main_layout = parse_layout_form(main_columns, main_line_numbers, main_double_spacing)
        supplement_layout = parse_layout_form(
            supplement_columns, supplement_line_numbers, supplement_double_spacing
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # combine needs both a supplement and a compile step (mirror the CLI).
    if combine and supplement is None:
        raise HTTPException(status_code=400, detail="combine requires a supplement file")
    if combine and not pdf:
        raise HTTPException(status_code=400, detail="combine requires pdf compilation")
    # Demo: never write to a caller-chosen path on a shared host. Checked up
    # front so the expensive conversion never runs just to be refused.
    if demo and export_dir and export_dir.strip():
        raise HTTPException(status_code=403, detail=_DEMO_FS_DISABLED)
    if len(figures) != len(figure_numbers):
        raise HTTPException(
            status_code=400,
            detail=(
                f"figures ({len(figures)}) and figure_numbers "
                f"({len(figure_numbers)}) must have the same length"
            ),
        )

    allowed_manuscripts = ", ".join("." + e for e in sorted(_ALLOWED_MANUSCRIPT_EXTS))
    if _lower_ext(main.filename) not in _ALLOWED_MANUSCRIPT_EXTS:
        raise HTTPException(
            status_code=400, detail=f"main manuscript must be one of: {allowed_manuscripts}"
        )
    if supplement is not None and _lower_ext(supplement.filename) not in _ALLOWED_MANUSCRIPT_EXTS:
        raise HTTPException(
            status_code=400, detail=f"supplement must be one of: {allowed_manuscripts}"
        )
    if references is not None and _lower_ext(references.filename) not in _ALLOWED_REFERENCE_EXTS:
        raise HTTPException(
            status_code=400,
            detail="references must be one of: "
            + ", ".join("." + e for e in sorted(_ALLOWED_REFERENCE_EXTS)),
        )
    if any(n <= 0 for n in figure_numbers):
        raise HTTPException(status_code=400, detail="figure numbers must be positive")
    if len(set(figure_numbers)) != len(figure_numbers):
        raise HTTPException(status_code=400, detail="figure numbers must be unique")
    for fig_upload in figures:
        fig_ext = _lower_ext(fig_upload.filename)
        if fig_ext not in _ALLOWED_FIGURE_EXTS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"unsupported figure type '.{fig_ext or '?'}' "
                    f"({fig_upload.filename or 'figure'}); allowed: "
                    + ", ".join(sorted(_ALLOWED_FIGURE_EXTS))
                ),
            )
    return main_layout, supplement_layout


async def stage_multi_uploads(
    *,
    root: Path,
    max_upload_bytes: int,
    main: UploadFile,
    supplement: UploadFile | None,
    references: UploadFile | None,
    figures: list[UploadFile],
    figure_numbers: list[int],
) -> StagedUploads:
    """Stream one request's uploads into a fresh session directory under ``root``.

    Names on disk are server-selected, never client basenames -- but the
    EXTENSION must survive: :mod:`latextify.ingest.formats` routes pandoc
    purely off it, so a widened-format upload forced to ``.docx`` would be
    misread as corrupt.

    Figure files land as ``figures/fig<N>.<ext>`` beside the main manuscript so
    the existing folder-convention override picks them up. Note that an
    override REPLACES an embedded figure -- a manuscript with no embedded image
    for figure N has nothing to attach the dropped file to.

    An oversized or failed upload removes the session directory before
    re-raising, so a rejected request never orphans one.
    """
    session_dir = root / uuid.uuid4().hex
    upload_dir = session_dir / "upload"
    upload_dir.mkdir(parents=True, exist_ok=True)

    supplement_path: Path | None = None
    references_path: Path | None = None
    try:
        main_path = upload_dir / f"main.{_lower_ext(main.filename)}"
        await _stream_upload(main, main_path, max_bytes=max_upload_bytes)

        if supplement is not None:
            supplement_path = upload_dir / f"supplement.{_lower_ext(supplement.filename)}"
            await _stream_upload(supplement, supplement_path, max_bytes=max_upload_bytes)

        if references is not None:
            references_path = upload_dir / f"references.{_lower_ext(references.filename)}"
            await _stream_upload(references, references_path, max_bytes=max_upload_bytes)

        if figures:
            # Numbers are validated positive+unique upstream, so these are unique.
            figures_override_dir = upload_dir / "figures"
            figures_override_dir.mkdir(exist_ok=True)
            for fig_upload, number in zip(figures, figure_numbers, strict=True):
                ext = _lower_ext(fig_upload.filename)
                if ext == "jpeg":  # normalize deliberately so fig<N>.jpg is canonical
                    ext = "jpg"
                await _stream_upload(
                    fig_upload,
                    figures_override_dir / f"fig{number}.{ext}",
                    max_bytes=max_upload_bytes,
                )
    except Exception:  # an oversized/failed upload must not orphan the session dir
        _rmtree(session_dir)
        raise

    return StagedUploads(
        session_dir=session_dir,
        main_path=main_path,
        supplement_path=supplement_path,
        references_path=references_path,
    )
