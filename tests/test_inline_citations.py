from latextify.citations.plaintext import PlaintextResult
from latextify.emit import citation_resolution
from latextify.emit.inline_supplement import BOUNDARY_MARKER
from latextify.model.refs import Name, RefEntry


def _entry(key: str, title: str) -> RefEntry:
    return RefEntry(
        key=key,
        entry_type="article",
        title=title,
        authors=(Name(family="Author"),),
        year="2020",
    )


def test_inline_plaintext_citations_use_each_segments_reference_numbers(monkeypatch, tmp_path):
    main = PlaintextResult(
        entries=[_entry("mainkey", "Main paper")],
        keys_by_number={1: "mainkey"},
        has_reference_list=True,
        supplement=False,
    )
    supplement = PlaintextResult(
        entries=[_entry("suppkey", "Supplement paper")],
        keys_by_number={1: "suppkey"},
        has_reference_list=True,
        supplement=True,
    )
    monkeypatch.setattr(
        citation_resolution,
        "reconstruct_citation_lists",
        lambda *_args, **_kwargs: (main, supplement),
    )
    tex = (
        "Main cites {[}1{]}.\n\\section{References}\nMain reference.\n"
        + BOUNDARY_MARKER
        + "\n\\section*{Supplementary Information}\nSI cites {[}1{]}.\n"
        "\\section{References}\nSI reference."
    )

    entries, linked, warnings, _records = citation_resolution.link_inline_plaintext_citations(
        tmp_path / "merged.docx", tex, None
    )

    assert [entry.key for entry in entries] == ["mainkey", "suppkey"]
    assert "Main cites \\cite{mainkey}." in linked
    assert "SI cites \\cite{suppkey}." in linked
    assert "Main reference" not in linked
    assert "SI reference" not in linked
    assert len(warnings) == 1
    assert "continuous document-wide numbering" in warnings[0].message
