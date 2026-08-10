"""paper.yaml schema: validation on load, ``# CHECK:``-annotated rendering on write.

Split out of :mod:`latextify.ingest.metadata_guess` (2026-08-10), which sits
at its own line-count ratchet pin (``tests/test_repo_integrity.py``). Kept
together because the two directions mirror each other field-for-field:
:func:`meta_from_yaml_data` is what
:func:`latextify.ingest.metadata_guess.load_meta` runs against a sidecar a
human may have hand-edited, and :func:`render_paper_yaml` is what
:func:`latextify.ingest.metadata_guess.load_or_create_meta` runs to write one
for the first time -- a schema change to one side without the other silently
breaks the round trip.

IR convention (also noted in :mod:`latextify.model.meta`):
``Author.affiliations`` are 0-based indices into ``Meta.affiliations``. The
paper.yaml FILE stays 1-based, matching the visible superscript markers in
the manuscript (affiliation 1, 2, ...); the +/-1 conversion happens only at
this boundary, in :func:`meta_from_yaml_data` (read) and
:func:`_author_to_dict` (write).

:class:`MetaValidationError` always names the offending field (e.g.
``authors[0].affiliations[0]``) so a hand-edit mistake is actionable without
re-running the guesser.
"""

from __future__ import annotations

import yaml

from latextify.model.meta import Affiliation, Author, Meta

#: paper.yaml sidecar filename, expected beside the source manuscript.
DEFAULT_SIDECAR_NAME = "paper.yaml"

_TOP_LEVEL_FIELD_ORDER = ("title", "authors", "affiliations", "abstract", "keywords")


class MetaValidationError(ValueError):
    """Raised when a paper.yaml sidecar fails schema validation.

    The message always names the offending field (e.g. ``authors[0].name``)
    so the error is actionable without having to open the file.
    """


def _field_error(source: str, field_name: str, msg: str) -> MetaValidationError:
    return MetaValidationError(f"{source}: field '{field_name}' {msg}")


def meta_from_yaml_data(data: object, *, source: str = DEFAULT_SIDECAR_NAME) -> Meta:
    """Validate a parsed-YAML mapping against the paper.yaml schema and build a Meta."""
    if not isinstance(data, dict):
        raise MetaValidationError(f"{source}: root must be a mapping, got {type(data).__name__}")

    if "title" not in data:
        raise MetaValidationError(f"{source}: missing required field 'title'")
    title = data["title"]
    if not isinstance(title, str) or not title.strip():
        raise _field_error(source, "title", "must be a non-empty string")

    if "affiliations" not in data:
        raise MetaValidationError(f"{source}: missing required field 'affiliations'")
    raw_affiliations = data["affiliations"]
    if not isinstance(raw_affiliations, list):
        raise _field_error(source, "affiliations", "must be a list of strings")
    affiliations: list[str] = []
    for i, item in enumerate(raw_affiliations):
        if not isinstance(item, str) or not item.strip():
            raise MetaValidationError(
                f"{source}: field 'affiliations[{i}]' must be a non-empty string"
            )
        affiliations.append(item)

    if "authors" not in data:
        raise MetaValidationError(f"{source}: missing required field 'authors'")
    raw_authors = data["authors"]
    if not isinstance(raw_authors, list) or not raw_authors:
        raise _field_error(source, "authors", "must be a non-empty list")

    authors: list[Author] = []
    for i, raw in enumerate(raw_authors):
        prefix = f"authors[{i}]"
        if not isinstance(raw, dict):
            raise MetaValidationError(f"{source}: field '{prefix}' must be a mapping")

        if "name" not in raw:
            raise MetaValidationError(f"{source}: missing required field '{prefix}.name'")
        name = raw["name"]
        if not isinstance(name, str) or not name.strip():
            raise MetaValidationError(f"{source}: field '{prefix}.name' must be a non-empty string")

        raw_affs = raw.get("affiliations", [])
        if not isinstance(raw_affs, list):
            raise MetaValidationError(
                f"{source}: field '{prefix}.affiliations' must be a list of integers"
            )
        affs: list[int] = []
        for j, aff_idx in enumerate(raw_affs):
            if not isinstance(aff_idx, int) or isinstance(aff_idx, bool):
                raise MetaValidationError(
                    f"{source}: field '{prefix}.affiliations[{j}]' must be an integer"
                )
            if not (1 <= aff_idx <= len(affiliations)):
                raise MetaValidationError(
                    f"{source}: field '{prefix}.affiliations[{j}]' references affiliation "
                    f"{aff_idx} but only {len(affiliations)} affiliation(s) are defined"
                )
            affs.append(aff_idx - 1)  # YAML is 1-based; the IR is 0-based

        email = raw.get("email")
        if email is not None and not isinstance(email, str):
            raise MetaValidationError(f"{source}: field '{prefix}.email' must be a string")

        corresponding = raw.get("corresponding", False)
        if not isinstance(corresponding, bool):
            raise MetaValidationError(f"{source}: field '{prefix}.corresponding' must be a boolean")

        authors.append(
            Author(name=name, affiliations=tuple(affs), email=email, corresponding=corresponding)
        )

    abstract = data.get("abstract", "")
    if not isinstance(abstract, str):
        raise _field_error(source, "abstract", "must be a string")

    raw_keywords = data.get("keywords", [])
    if not isinstance(raw_keywords, list):
        raise _field_error(source, "keywords", "must be a list of strings")
    keywords: list[str] = []
    for i, kw in enumerate(raw_keywords):
        if not isinstance(kw, str) or not kw.strip():
            raise MetaValidationError(f"{source}: field 'keywords[{i}]' must be a non-empty string")
        keywords.append(kw)

    return Meta(
        title=title,
        authors=tuple(authors),
        affiliations=tuple(Affiliation(name=a) for a in affiliations),
        abstract=abstract,
        keywords=tuple(keywords),
    )


def _author_to_dict(author: Author) -> dict:
    data: dict = {"name": author.name, "affiliations": [i + 1 for i in author.affiliations]}
    if author.email:
        data["email"] = author.email
    if author.corresponding:
        data["corresponding"] = True
    return data


def render_paper_yaml(meta: Meta, checks: dict[str, list[str]] | None = None) -> str:
    """Render Meta as paper.yaml text, with '# CHECK:' comments for low-confidence fields."""
    checks = checks or {}
    doc = {
        "title": meta.title,
        "authors": [_author_to_dict(a) for a in meta.authors],
        "affiliations": [a.name for a in meta.affiliations],
        "abstract": meta.abstract,
        "keywords": list(meta.keywords),
    }
    base = yaml.safe_dump(
        doc, sort_keys=False, allow_unicode=True, default_flow_style=False, width=88
    )
    lines = base.splitlines()

    # Insert bottom-to-top so already-computed indices for earlier fields stay valid.
    for field_name in reversed(_TOP_LEVEL_FIELD_ORDER):
        messages = checks.get(field_name)
        if not messages:
            continue
        marker = f"{field_name}:"
        insert_at = next(
            (i for i, line in enumerate(lines) if line == marker or line.startswith(marker + " ")),
            None,
        )
        if insert_at is None:
            continue
        lines[insert_at:insert_at] = [f"# CHECK: {msg}" for msg in messages]

    return "\n".join(lines) + "\n"
