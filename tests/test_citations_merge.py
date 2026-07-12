"""Tests for latextify.citations.merge (plan item 21).

merge_ref_entries dedupes a second document's RefEntry list against an
already-emitted document's, reusing citations.fields.dedup_identity so the
identity rule is identical to the one used to dedupe WITHIN one document.
"""

from __future__ import annotations

from latextify.citations.bib import next_available_key
from latextify.citations.merge import merge_ref_entries
from latextify.model.refs import Name, RefEntry


def _entry(key: str, **overrides) -> RefEntry:
    defaults = dict(
        key=key,
        entry_type="article",
        csl_type="article-journal",
        title="A Test Paper",
        authors=(Name(family="Smith", given="Alice"),),
        year="2020",
        doi=None,
        raw_id=None,
    )
    defaults.update(overrides)
    return RefEntry(**defaults)


# --------------------------------------------------------------------------- #
# next_available_key
# --------------------------------------------------------------------------- #


class TestNextAvailableKey:
    def test_returns_base_when_unused(self):
        assert next_available_key("smith2020test", set()) == "smith2020test"

    def test_suffixes_on_collision(self):
        assert next_available_key("smith2020test", {"smith2020test"}) == "smith2020testa"

    def test_finds_first_free_suffix(self):
        used = {"smith2020test", "smith2020testa", "smith2020testb"}
        assert next_available_key("smith2020test", used) == "smith2020testc"

    def test_wraps_past_z_like_bib_suffix(self):
        used = {"k"} | {f"k{chr(ord('a') + i)}" for i in range(26)}
        assert next_available_key("k", used) == "kza"


# --------------------------------------------------------------------------- #
# merge_ref_entries: dedup by DOI
# --------------------------------------------------------------------------- #


class TestMergeByDoi:
    def test_shared_doi_deduplicates_to_one_entry(self):
        base = [_entry("muller2020quantum", doi="10.1103/PhysRevB.101.045123")]
        # A different key/title text but the SAME DOI -- still a duplicate.
        new = [
            _entry(
                "someothersikey",
                title="Quantum transport in GaAs heterostructures (SI reprint)",
                doi="10.1103/PhysRevB.101.045123",
            )
        ]

        merged, remap = merge_ref_entries(base, new)

        assert len(merged) == 1
        assert merged[0].key == "muller2020quantum"
        assert remap == {"someothersikey": "muller2020quantum"}

    def test_doi_match_is_case_and_whitespace_insensitive(self):
        base = [_entry("k1", doi="10.1103/PhysRevB.101.045123")]
        new = [_entry("k2", doi="  10.1103/PHYSREVB.101.045123  ")]

        merged, remap = merge_ref_entries(base, new)

        assert len(merged) == 1
        assert remap == {"k2": "k1"}

    def test_different_doi_is_kept_as_new(self):
        base = [_entry("k1", doi="10.1103/PhysRevB.101.045123")]
        new = [_entry("k2", doi="10.1103/PhysRevApplied.15.054001")]

        merged, remap = merge_ref_entries(base, new)

        assert [e.key for e in merged] == ["k1", "k2"]
        assert remap == {"k2": "k2"}


# --------------------------------------------------------------------------- #
# merge_ref_entries: dedup by raw_id / fingerprint fallback
# --------------------------------------------------------------------------- #


class TestMergeByRawIdAndFingerprint:
    def test_matching_raw_id_deduplicates_when_no_doi(self):
        base = [_entry("k1", doi=None, raw_id="ITEM-42")]
        new = [_entry("k2", doi=None, raw_id="ITEM-42")]

        merged, remap = merge_ref_entries(base, new)

        assert len(merged) == 1
        assert remap == {"k2": "k1"}

    def test_matching_author_year_title_fingerprint_deduplicates(self):
        base = [
            _entry(
                "k1",
                doi=None,
                raw_id=None,
                authors=(Name(family="Kittel", given="Charles"),),
                year="2005",
                title="Introduction to Solid State Physics",
            )
        ]
        new = [
            _entry(
                "k2",
                doi=None,
                raw_id=None,
                authors=(Name(family="Kittel", given="Charles"),),
                year="2005",
                title="Introduction to Solid State Physics",
            )
        ]

        merged, remap = merge_ref_entries(base, new)

        assert len(merged) == 1
        assert remap == {"k2": "k1"}

    def test_two_entries_with_no_identifying_data_never_merge(self):
        # dedup_identity's "unidentified" fallback is a fresh counter value
        # every call -- two bare entries in base vs. new must never collapse.
        base = [_entry("k1", doi=None, raw_id=None, authors=(), year=None, title=None)]
        new = [_entry("k2", doi=None, raw_id=None, authors=(), year=None, title=None)]

        merged, remap = merge_ref_entries(base, new)

        assert len(merged) == 2
        assert remap == {"k2": "k2"}


# --------------------------------------------------------------------------- #
# merge_ref_entries: key collisions, ordering, base-entries-untouched
# --------------------------------------------------------------------------- #


class TestMergeKeyCollisions:
    def test_new_entry_key_colliding_with_base_key_gets_resuffixed(self):
        # Different references that happen to already carry the same key
        # (e.g. assigned independently within their own documents).
        base = [_entry("smith2020test", doi="10.1/aaa")]
        new = [_entry("smith2020test", doi="10.1/bbb")]

        merged, remap = merge_ref_entries(base, new)

        assert [e.key for e in merged] == ["smith2020test", "smith2020testa"]
        assert remap == {"smith2020test": "smith2020testa"}
        # The base entry's key/content is completely untouched.
        assert merged[0] is base[0]

    def test_base_entries_are_never_mutated_or_reordered(self):
        base = [_entry("k1", doi="10.1/aaa"), _entry("k2", doi="10.1/bbb")]
        new = [_entry("k3", doi="10.1/ccc")]

        merged, remap = merge_ref_entries(base, new)

        assert merged[0] is base[0]
        assert merged[1] is base[1]
        assert merged[2].key == "k3"
        assert remap == {"k3": "k3"}

    def test_empty_new_entries_returns_base_unchanged(self):
        base = [_entry("k1", doi="10.1/aaa")]
        merged, remap = merge_ref_entries(base, [])
        assert merged == base
        assert remap == {}

    def test_empty_base_entries_keeps_all_new_entries(self):
        new = [_entry("k1", doi="10.1/aaa"), _entry("k2", doi="10.1/bbb")]
        merged, remap = merge_ref_entries([], new)
        assert [e.key for e in merged] == ["k1", "k2"]
        assert remap == {"k1": "k1", "k2": "k2"}

    def test_key_collision_skips_past_an_already_used_suffix(self):
        # Both "a" and "aa" are already used (e.g. two unrelated base
        # entries that happen to share that base key from their own
        # document); a new entry whose own key is "a" must skip past both
        # and land on "ab", not stop at the first (already-taken) suffix.
        base = [_entry("a", doi="10.1/base1"), _entry("aa", doi="10.1/base2")]
        new = [_entry("a", doi="10.1/new1")]

        merged, remap = merge_ref_entries(base, new)

        assert [e.key for e in merged] == ["a", "aa", "ab"]
        assert remap == {"a": "ab"}
