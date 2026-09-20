"""Citation quality passes that sit outside the field-coded happy path.

Split out of :mod:`latextify.emit.project` (2026-08-10). Two independent
passes live here, grouped because both are optional/degrading layers around
the core citation pipeline rather than part of it -- neither can fail an
otherwise-successful emit, they only add warnings:

:func:`link_plaintext_citations` -- plan item 14's no-field-codes fallback.
    When a document carries no Zotero/Mendeley/EndNote field codes at all
    (:func:`latextify.citations.fields.extract_field_citations` found
    nothing), there is no structured citation data to link from -- so this
    reconstructs one from the document's own typed reference list via
    Crossref (:mod:`latextify.citations.plaintext`), strips that now-duplicate
    typed list from the body, and rewrites whatever in-text marker shape the
    author used (``{[}12{]}``, ``\\textsuperscript{...}``, ``(Smith et al.,
    2020)``) into ``\\cite{...}``. Both the main document and the supplement
    (:mod:`latextify.emit.supplement`) call this independently -- each
    document's typed list is reconstructed and linked on its own, before plan
    item 21's cross-document reference merge happens in the caller.

:func:`run_reference_validation` -- the opt-in ``--check-references`` pass.
    Runs once, after the caller has assembled (and, if a supplement exists,
    merged) the final bibliography, so every reference -- main and SI alike --
    is checked against Crossref exactly once. Never propagates a failure: an
    unreachable Crossref degrades to an all-``unchecked`` report plus one
    advisory warning rather than sinking an otherwise-successful emit.

A reconstructed reference that Crossref could not confidently match still
gets emitted (from the raw typed text) rather than dropped, flagged ``verify``
so the author knows to hand-check it -- :func:`_verify_warnings` turns that
flag into the loud, per-reference warning that surfaces the review request.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from latextify.citations.body_markers import link_body_markers, strip_reference_section
from latextify.citations.crossref import CrossrefClient
from latextify.citations.merge import merge_ref_entries
from latextify.citations.plaintext import reconstruct_citation_lists, reconstruct_citations
from latextify.citations.validate import validate_references
from latextify.model.emit import EmitWarning
from latextify.model.refs import RefEntry
from latextify.model.validate import ValidationReport

from .anchors import remap_cite_keys_in_text
from .inline_supplement import BOUNDARY_MARKER


def link_plaintext_citations(
    docx_path: Path, tex: str, mailto: str | None, bib_entries: list[RefEntry] | None = None
) -> tuple[list[RefEntry], str, list[EmitWarning], tuple]:
    """Reconstruct a typed bibliography and link its in-text markers.

    Returns the reconstructed ``.bib`` entries, the body with markers rewritten
    to ``\\cite{...}`` and the duplicate typed reference list removed, the
    accumulated warnings (unresolved markers + low-confidence ``verify`` refs),
    and the reconciliation records for the report.
    A document with no typed reference list yields no entries and an untouched
    body -- there is nothing to reconstruct or link. ``bib_entries`` (the
    author's parsed ``.bib``) is matched before Crossref when supplied.
    """
    result = reconstruct_citations(docx_path, mailto=mailto, bib_entries=bib_entries)
    if not result.has_reference_list:
        return [], tex, [], ()
    tex = strip_reference_section(tex, result)
    tex, messages = link_body_markers(tex, result)
    warnings = [EmitWarning(message=message) for message in messages]
    warnings.extend(_verify_warnings(result.records))
    return result.entries, tex, warnings, result.records


def link_inline_plaintext_citations(
    docx_path: Path, tex: str, mailto: str | None, bib_entries: list[RefEntry] | None = None
) -> tuple[list[RefEntry], str, list[EmitWarning], tuple]:
    """Link main and SI typed citation lists against their own body segments."""
    results = reconstruct_citation_lists(docx_path, mailto=mailto, bib_entries=bib_entries)
    if not results:
        return [], tex, [], ()
    if len(results) == 1 or BOUNDARY_MARKER not in tex:
        result = results[0]
        tex = strip_reference_section(tex, result)
        tex, messages = link_body_markers(tex, result)
        warnings = [EmitWarning(message=message) for message in messages]
        warnings.extend(_verify_warnings(result.records))
        return result.entries, tex, warnings, result.records

    main_tex, supplement_tex = tex.split(BOUNDARY_MARKER, 1)
    main_result = next((result for result in results if not result.supplement), None)
    supplement_result = next((result for result in results if result.supplement), None)
    warnings: list[EmitWarning] = []
    records = list(main_result.records) if main_result is not None else []
    entries: list[RefEntry] = []

    if main_result is not None:
        main_tex = strip_reference_section(main_tex, main_result)
        main_tex, messages = link_body_markers(main_tex, main_result)
        warnings.extend(EmitWarning(message=message) for message in messages)
        warnings.extend(_verify_warnings(main_result.records))
        entries = list(main_result.entries)

    if supplement_result is not None:
        supplement_tex = strip_reference_section(supplement_tex, supplement_result)
        supplement_tex, messages = link_body_markers(supplement_tex, supplement_result)
        warnings.extend(EmitWarning(message=f"supplement: {message}") for message in messages)
        warnings.extend(_verify_warnings(supplement_result.records))
        merged, key_remap = merge_ref_entries(entries, supplement_result.entries)
        entries = merged
        supplement_tex = remap_cite_keys_in_text(supplement_tex, key_remap)
        records.extend(
            replace(record, key=key_remap.get(record.key, record.key))
            for record in supplement_result.records
        )

    if len(results) > 2:
        warnings.append(
            EmitWarning(
                message=(
                    "more than one reference list was found in a document segment; "
                    "only the first main and first supplementary lists were linked"
                )
            )
        )
    return entries, main_tex + BOUNDARY_MARKER + supplement_tex, warnings, tuple(records)


def run_reference_validation(
    entries: list[RefEntry], mailto: str | None
) -> tuple[ValidationReport | None, list[EmitWarning]]:
    """Validate the assembled bibliography online (opt-in ``--check-references``).

    Opens a single Crossref client, validates every entry serially, and returns
    the report plus any user-facing warnings. Never propagates a failure: a
    fully offline run yields an all-``unchecked`` report (with one advisory
    warning), and any unexpected error degrades to ``None`` + a warning rather
    than failing an otherwise-successful emit -- reference checking is a bonus
    pass, never a gate.
    """
    try:
        with CrossrefClient(mailto=mailto) as client:
            report = validate_references(entries, client)
    except Exception as exc:  # never let a bonus check sink the whole emit
        return None, [
            EmitWarning(
                message=(
                    "online reference check could not run "
                    f"({type(exc).__name__}: {exc}); skipped. References were not verified."
                )
            )
        ]
    warnings: list[EmitWarning] = []
    if not report.any_checked:
        warnings.append(
            EmitWarning(
                message=(
                    "online reference check requested but Crossref was unreachable; "
                    "no references were verified (all marked unchecked)."
                )
            )
        )
    elif report.count("unchecked"):
        warnings.append(
            EmitWarning(
                message=(
                    f"{report.count('unchecked')} of {report.total} reference(s) could not "
                    "be checked (Crossref errors mid-run); see the unchecked entries in "
                    "report.md."
                )
            )
        )
    return report, warnings


def _verify_warnings(records) -> list[EmitWarning]:
    """One loud warning per below-threshold (``verify``) reconstructed reference."""
    warnings: list[EmitWarning] = []
    for record in records:
        if not record.verify:
            continue
        number = f" [{record.ref_number}]" if record.ref_number is not None else ""
        warnings.append(
            EmitWarning(
                message=(
                    f"reference{number} could not be confidently matched to Crossref "
                    f"(best score {record.score:.2f}); emitted from raw text -- verify "
                    f"the references.bib entry '{record.key}'."
                )
            )
        )
    return warnings
