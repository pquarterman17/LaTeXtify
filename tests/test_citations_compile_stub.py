"""End-to-end citation test THROUGH COMPILE — deferred stub.

Plan item 7's final sub-task ("End-to-end fixture test through compile") cannot
run until the parallel sibling items land:

    * item 3 (pandoc body pipeline) — emits ``%%CITE:<idx>%%`` anchors in the
      body in document order;
    * item 5 (project emitter) — pairs each ``%%CITE:<idx>%%`` anchor with the
      matching Citation.index and writes references.bib into the output tree;
    * item 6 (Tectonic compile wrapper) — builds the PDF and lets us assert the
      bibliography renders with clickable DOI links.

When those exist, replace the skip with: convert zotero_cited.docx through the
full pipeline, assert grep for ``%%CITE`` finds nothing in the generated body,
that ``\\cite{muller2020quantum}`` (etc.) appears in document order, and that
the compiled PDF contains the DOI hyperlinks. The extraction half is already
covered by test_citations_extract.py.
"""

import pytest

pytestmark = pytest.mark.skip(
    reason="Requires sibling items 3 (body pipeline), 5 (emitter), 6 (compile) — not yet merged."
)


def test_zotero_cited_compiles_with_doi_links():
    raise AssertionError("unreachable: skipped until items 3/5/6 land")
