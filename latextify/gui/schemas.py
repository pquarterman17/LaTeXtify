"""Request/response models for the GUI API (extracted from ``server.py``).

Pure pydantic shapes -- no conversion logic, no FastAPI app state. Moved out of
:mod:`latextify.gui.server` to keep that module under its size-ratchet pin; the
endpoint handlers that populate these live there.
"""

from __future__ import annotations

from pydantic import BaseModel


class JournalInfo(BaseModel):
    """One entry of ``GET /api/journals``."""

    name: str
    display_name: str
    modes: list[str]


class ConvertResponse(BaseModel):
    """Body of ``POST /api/convert``."""

    output_dir: str
    warnings: list[str]
    report_md: str
    success: bool
    pdf_url: str | None = None


class ConvertMultiResponse(BaseModel):
    """Body of ``POST /api/convert-multi`` (the multi-file intake).

    Every ``*_url`` is a server-issued download token or ``None`` when that
    artifact was not produced (no supplement, combine off, audit off, zip off,
    or a compile that failed). ``success`` is ``True`` only when EVERY requested
    compilation succeeded -- a main-success-but-supplement-failure run reports
    ``success=False`` (partial), never a misleading success. The per-document
    ``main_compile_success`` / ``supplement_compile_success`` fields give the
    breakdown (``None`` when that document was not compiled). ``success`` is
    ``True`` when ``--pdf`` was off and emission succeeded.
    """

    output_dir: str
    warnings: list[str]
    report_md: str
    success: bool
    #: Per-document compile outcomes; None when that document was not compiled.
    main_compile_success: bool | None = None
    supplement_compile_success: bool | None = None
    pdf_url: str | None = None
    supplement_pdf_url: str | None = None
    combined_pdf_url: str | None = None
    audit_pdf_url: str | None = None
    zip_url: str | None = None
    #: Folder the selected artifacts were copied to (when an inline export was requested).
    exported_to: str | None = None
    #: Names of the artifacts copied to ``exported_to``.
    exported: list[str] = []
    #: Opaque handle to this conversion's produced artifacts, for a later
    #: ``POST /api/export`` (the preview-then-export flow). None only if the
    #: session store is somehow unavailable.
    export_token: str | None = None
    #: Structured online reference-validation results (None unless the run was
    #: asked to check references). Drives the interactive review panel.
    validation: ValidationOut | None = None


class PickFolderResponse(BaseModel):
    """Body of ``POST /api/pick-folder``. ``path`` is "" when cancelled/unavailable."""

    path: str


class ExportRequest(BaseModel):
    """Body of ``POST /api/export`` -- copy a prior conversion's artifacts out.

    ``export_token`` is the handle returned by ``/api/convert-multi``; it maps,
    server side only, to that run's produced artifacts. This lets the UI preview
    a conversion first and export the *same* result afterwards without
    recompiling.
    """

    export_token: str
    export_dir: str
    export_types: list[str] = []


class ExportResponse(BaseModel):
    """Body of ``POST /api/export``."""

    exported_to: str
    exported: list[str]
    warnings: list[str] = []


class FieldProblemOut(BaseModel):
    """One field of a flagged reference that disagrees with Crossref."""

    field: str
    ours: str
    canonical: str


class ValidationRecordOut(BaseModel):
    """One flagged reference, with the data the review UI needs to act on."""

    key: str
    status: str
    doi: str | None = None
    suggested_doi: str | None = None
    problems: list[FieldProblemOut] = []
    #: The current reference as flat editable fields (title, authors, ...).
    entry: dict[str, str]
    #: Crossref's version as the same flat fields (``None`` when there is no
    #: canonical record, i.e. a dead DOI or an unverifiable reference).
    canonical: dict[str, str] | None = None


class ValidationOut(BaseModel):
    """Structured reference-validation results for the review panel."""

    total: int
    flagged: int
    counts: dict[str, int]
    records: list[ValidationRecordOut] = []  # flagged references only


class CorrectionDecisionIn(BaseModel):
    """One author decision posted to ``/api/apply-corrections``.

    ``action`` is ``approve`` | ``deny`` | ``edit``. ``entry`` (the flat edited
    fields) is required only for ``edit``.
    """

    key: str
    action: str
    entry: dict[str, str] | None = None


class ApplyCorrectionsRequest(BaseModel):
    """Body of ``POST /api/apply-corrections``."""

    export_token: str
    decisions: list[CorrectionDecisionIn] = []


class ApplyCorrectionsResponse(BaseModel):
    """Body of ``POST /api/apply-corrections``."""

    applied: int
    success: bool
    pdf_url: str | None = None
    supplement_pdf_url: str | None = None
    combined_pdf_url: str | None = None
    warnings: list[str] = []
