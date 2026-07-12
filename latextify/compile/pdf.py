"""Staple compiled PDFs into one file (the ``--combine-supplement`` option).

The main document and the Supplementary Material are compiled as two separate
LaTeX documents (each keeps its own title page, its correct figure numbering --
``1, 2, ...`` vs ``S1, S2, ...`` -- and its own reference list). When the user
asks for a single combined PDF, the robust move is to compile both as usual and
concatenate the resulting PDFs, rather than fold two documents with different
numbering and separate bibliographies into one fragile ``.tex`` that would have
to special-case every journal class (REVTeX's ``\\maketitle`` runs once; two
``\\bibliography`` calls need extra packages). pypdf (BSD, pure-Python) does the
concatenation.
"""

from __future__ import annotations

from pathlib import Path


def staple_pdfs(parts: list[Path | str], dest: Path | str) -> Path:
    """Concatenate ``parts`` (in order) into a single PDF written to ``dest``.

    Returns ``dest``. Raises :class:`FileNotFoundError` if any part is missing
    (a caller should only staple PDFs it just compiled successfully). pypdf is
    imported lazily so the dependency is only exercised when combining.
    """
    from pypdf import PdfWriter

    dest = Path(dest)
    resolved = [Path(part) for part in parts]
    missing = [str(p) for p in resolved if not p.is_file()]
    if missing:
        raise FileNotFoundError(f"cannot staple PDFs; missing input(s): {', '.join(missing)}")

    writer = PdfWriter()
    try:
        for part in resolved:
            writer.append(str(part))
        with dest.open("wb") as handle:
            writer.write(handle)
    finally:
        writer.close()
    return dest
