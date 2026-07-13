"""Apply reviewed reference corrections back onto the bibliography.

The online validator (:mod:`latextify.citations.validate`) flags references that
disagree with Crossref. The interactive review (CLI prompt / GUI panel) collects
one :class:`~latextify.model.validate.CorrectionDecision` per flagged reference;
this module turns those decisions into a corrected entry list, which the caller
re-emits to ``references.bib`` (and recompiles).

Three decisions, mirroring what the author sees:

* **approve** -- adopt Crossref's canonical value for every flagged field, plus
  the suggested DOI when the reference had none. The structured canonical data
  comes from the record's ``canonical_entry`` (not the truncated display
  strings), so an approved author-list correction restores the *full* author
  tuple, not "Smith, Jones, +3".
* **deny** -- leave the entry untouched.
* **edit** -- replace the entry wholesale with the author's edited version
  (every field), keying it back to the original so in-text ``\\cite`` keys and
  the compiled body stay valid.

The entry <-> flat-dict helpers (:func:`entry_to_dict` / :func:`entry_from_dict`)
are the wire/prompt shape: the GUI edits a JSON object and the CLI edits the
same fields, both round-tripping through one representation so the two front
ends cannot drift.
"""

from __future__ import annotations

from dataclasses import replace

from ..model.refs import Name, RefEntry
from ..model.validate import CorrectionDecision, ValidationRecord, ValidationReport

# Maps a FieldCheck.field name to the RefEntry attribute it corrects. "journal"
# is stored as container_title; the rest match one-to-one.
_FIELD_TO_ATTR = {
    "title": "title",
    "authors": "authors",
    "year": "year",
    "journal": "container_title",
    "volume": "volume",
    "issue": "issue",
    "pages": "pages",
}

# The editable fields exposed in the whole-entry editor, in display order. Keys
# are what entry_to_dict emits / entry_from_dict reads.
EDITABLE_FIELDS = ("title", "authors", "year", "journal", "volume", "issue", "pages", "doi")


def authors_to_text(names: tuple[Name, ...]) -> str:
    """Serialize an author tuple to one editable line: ``"Family, Given; ..."``.

    An institutional (literal) author is written as its literal string alone;
    a personal name as ``Family, Given`` (or just ``Family`` when no given name
    is known). Round-trips through :func:`authors_from_text`.
    """
    parts: list[str] = []
    for name in names:
        if name.literal and not name.family:
            parts.append(name.literal)
        elif name.given:
            parts.append(f"{name.family}, {name.given}")
        elif name.family:
            parts.append(name.family)
    return "; ".join(parts)


def authors_from_text(text: str) -> tuple[Name, ...]:
    """Parse ``"Family, Given; Family, Given"`` back into an author tuple.

    Segments split on ``;``; within a segment a comma separates family from
    given. A comma-less segment becomes a family-only name. (An institutional
    author typed without a comma round-trips as a family-only name -- a benign
    simplification; author-list edits are rare next to year/DOI/page fixes.)
    """
    out: list[Name] = []
    for segment in text.split(";"):
        segment = segment.strip()
        if not segment:
            continue
        if "," in segment:
            family, given = segment.split(",", 1)
            out.append(Name(family=family.strip(), given=given.strip()))
        else:
            out.append(Name(family=segment))
    return tuple(out)


def entry_to_dict(entry: RefEntry) -> dict[str, str]:
    """Flatten a RefEntry to the editable string fields (empty string for None)."""
    return {
        "key": entry.key,
        "title": entry.title or "",
        "authors": authors_to_text(entry.authors),
        "year": entry.year or "",
        "journal": entry.container_title or "",
        "volume": entry.volume or "",
        "issue": entry.issue or "",
        "pages": entry.pages or "",
        "doi": entry.doi or "",
    }


def entry_from_dict(data: dict[str, str], *, base: RefEntry) -> RefEntry:
    """Rebuild a RefEntry from edited fields, preserving ``base``'s identity.

    ``base`` supplies the fields the editor does not expose (``entry_type``,
    ``csl_type``, ``source``, ``key``, ...) so an edit never silently drops
    provenance or the citation key. Blank strings become ``None``.
    """

    def clean(name: str) -> str | None:
        value = (data.get(name) or "").strip()
        return value or None

    return replace(
        base,
        title=clean("title"),
        authors=authors_from_text(data.get("authors", "")),
        year=clean("year"),
        container_title=clean("journal"),
        volume=clean("volume"),
        issue=clean("issue"),
        pages=clean("pages"),
        doi=clean("doi"),
    )


def _apply_canonical(entry: RefEntry, record: ValidationRecord) -> RefEntry:
    """Adopt Crossref's canonical value for each flagged field (+ suggested DOI)."""
    canonical = record.canonical_entry
    updates: dict[str, object] = {}
    if canonical is not None:
        for check in record.problems:
            attr = _FIELD_TO_ATTR.get(check.field)
            if attr is not None:
                updates[attr] = getattr(canonical, attr)
    # A doi_suggested reference's whole point is to gain the DOI.
    if record.status == "doi_suggested" and record.suggested_doi:
        updates["doi"] = record.suggested_doi
    if not updates:
        return entry
    return replace(entry, **updates)


def apply_corrections(
    entries: list[RefEntry],
    report: ValidationReport,
    decisions: list[CorrectionDecision],
) -> list[RefEntry]:
    """Return a new entry list with the author's accepted corrections applied.

    Entries keep their original order and count. An entry with no decision (or a
    ``deny``) is returned unchanged; ``approve`` adopts the canonical fields;
    ``edit`` swaps in the author's ``edited_entry`` (re-keyed to the original so
    ``\\cite`` keys stay valid). Decisions naming an unknown key are ignored.
    """
    records_by_key = {r.key: r for r in report.records}
    decisions_by_key = {d.key: d for d in decisions}
    out: list[RefEntry] = []
    for entry in entries:
        decision = decisions_by_key.get(entry.key)
        if decision is None or decision.action == "deny":
            out.append(entry)
        elif decision.action == "edit" and decision.edited_entry is not None:
            out.append(replace(decision.edited_entry, key=entry.key))
        elif decision.action == "approve":
            record = records_by_key.get(entry.key)
            out.append(_apply_canonical(entry, record) if record is not None else entry)
        else:
            out.append(entry)
    return out
