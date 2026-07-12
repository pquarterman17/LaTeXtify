"""Cross-document reference merging for supplementary material (plan item 21).

Deduplicates a second document's (a supplementary manuscript's) extracted
``RefEntry`` list against an already-emitted document's entries, using the
exact same identity rule :func:`latextify.citations.fields.dedup_identity`
uses to dedupe *within* one document (DOI -> raw_id -> author/year/title
fingerprint), so "the same reference cited in both the main paper and its
SI" collapses to one ``references.bib`` entry regardless of which document
is examined first.

``base_entries``' keys are never changed here -- they may already be baked
into an already-written ``body.tex`` (``\\cite{...}`` commands resolved
before this runs). A ``new_entries`` item that matches an existing base
identity is dropped; its own key is remapped (in the returned
``key_remap``) to the matching base entry's key. A genuinely new entry is
kept, using its own key unless that key collides with something already
used (either a base key or another kept new entry), in which case a fresh
a/b/c-suffixed key is assigned via :func:`latextify.citations.bib.next_available_key`.
"""

from __future__ import annotations

from dataclasses import replace

from ..model.refs import RefEntry
from .bib import next_available_key
from .fields import dedup_identity


def merge_ref_entries(
    base_entries: list[RefEntry], new_entries: list[RefEntry]
) -> tuple[list[RefEntry], dict[str, str]]:
    """Merge ``new_entries`` into ``base_entries``, deduping by :func:`dedup_identity`.

    Args:
        base_entries: the already-keyed entries of the document processed
            first (the main manuscript). Never mutated or re-keyed.
        new_entries: the already-keyed entries of the second document (the
            supplementary material), as returned by
            :func:`latextify.citations.fields.extract_field_citations` or
            reconstructed via :mod:`latextify.citations.plaintext`.

    Returns:
        A ``(merged_entries, key_remap)`` pair:

        * ``merged_entries`` -- ``base_entries`` (untouched, same order)
          followed by whichever ``new_entries`` were genuinely new (in their
          own order), each keeping its original key unless a collision
          forced a resuffix.
        * ``key_remap`` -- maps every ``new_entries[i].key`` (its ORIGINAL,
          pre-merge key) to its final key in ``merged_entries``: the
          matching base entry's key for a duplicate, or its own (possibly
          resuffixed) key for a new entry. Callers use this to rewrite the
          second document's already-resolved ``Citation.keys`` / literal
          ``\\cite{...}`` commands so they point at the merged bibliography.
    """
    identity_to_key: dict[str, str] = {}
    used_keys: set[str] = set()
    for entry in base_entries:
        identity_to_key[dedup_identity(entry)] = entry.key
        used_keys.add(entry.key)

    merged: list[RefEntry] = list(base_entries)
    remap: dict[str, str] = {}
    for entry in new_entries:
        identity = dedup_identity(entry)
        existing_key = identity_to_key.get(identity)
        if existing_key is not None:
            remap[entry.key] = existing_key
            continue
        final_key = next_available_key(entry.key, used_keys)
        used_keys.add(final_key)
        identity_to_key[identity] = final_key
        merged.append(entry if final_key == entry.key else replace(entry, key=final_key))
        remap[entry.key] = final_key

    return merged, remap
