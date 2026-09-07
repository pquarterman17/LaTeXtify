"""Filling in a figure the manuscript captions but never had an image for.

The failure this repairs is the worst one in the figure pipeline, because it is
silent and it corrupts a submission. VERIFIED on a generated fixture captioning
figures 1-4 with no image pasted for 3:

    * pandoc binds a caption to the image ADJACENT to it, so the image that
      belongs to caption 4 is emitted carrying **caption 3's text** -- and every
      later figure is shifted the same way;
    * the orphaned "Figure 3: ..." paragraph is left behind in the body as
      ordinary text, sitting exactly where its figure should have been.

:mod:`latextify.figures.caption_gaps` already detects the discrepancy and warns.
This module acts on it: given a replacement file for the missing figure, it

    1. renumbers the extracted figures from document order to the numbers the
       CAPTIONS state, which re-pairs every shifted caption with its own image;
    2. builds a record for the missing figure from the supplied file and the
       orphaned caption's own text; and
    3. rewrites the body so the orphan paragraph becomes that figure's anchor,
       and every existing anchor points at its renumbered figure.

Because LaTeX numbers floats by the order they appear, putting the float where
the orphan paragraph sat also makes the *printed* numbering come out right --
1, 2, 3, 4 -- with no ``\\setcounter`` trickery.

Nothing here activates unless a replacement file was actually supplied for a
gap. With no file the old behaviour stands exactly: the figures keep their
document-order numbers and the existing warning names the remedy. Repairing the
numbering only when we can also supply the missing image keeps this from
quietly reshaping a project an author has already been working with.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from latextify.figures.caption_gaps import _caption_paragraphs
from latextify.figures.override import OverrideSources, _resolve_one
from latextify.model.figure import Figure, FigureSource


@dataclass(frozen=True)
class GapFillPlan:
    """What filling this manuscript's caption gaps changes.

    Attributes:
        figures: the full figure set to emit -- existing records renumbered to
            their stated caption numbers, plus one new record per filled gap.
        renumbering: ``{old document-order number: new stated number}``, for
            rewriting the body's ``%%FIGURE:<n>%%`` anchors.
        filled: stated numbers a file was supplied for, ascending.
        unfilled: gap numbers still missing a file, so the caller can keep
            warning about those and only those.
        captions: ``{stated number: orphan caption text}`` for the figures
            created, used to replace the orphan paragraph in the body.
    """

    figures: tuple[Figure, ...]
    renumbering: dict[int, int] = field(default_factory=dict)
    filled: tuple[int, ...] = ()
    unfilled: tuple[int, ...] = ()
    captions: dict[int, str] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        """True when at least one gap was filled, so the body needs rewriting."""
        return bool(self.filled)


def _stated_to_document_order(docx_path: Path) -> tuple[dict[int, int], list[int]]:
    """Map each captioned figure to its document-order index, and list the gaps.

    Walks the caption paragraphs in order. A caption with an image beside it
    consumes the next extracted figure; one without is a gap. Returns
    ``({stated number: document-order number}, [gap numbers])``.
    """
    captions = _caption_paragraphs(docx_path)
    stated_to_order: dict[int, int] = {}
    gaps: list[int] = []
    order = 0
    for stated, has_image in captions:
        if has_image:
            order += 1
            stated_to_order[stated] = order
        else:
            gaps.append(stated)
    return stated_to_order, gaps


def plan_gap_fill(
    figures: tuple[Figure, ...],
    docx_path: Path | str,
    sources: OverrideSources | None = None,
    *,
    prefix: str = "",
) -> GapFillPlan:
    """Decide how to repair ``docx_path``'s caption gaps, if a file was supplied.

    Consults the same override tiers a normal figure does
    (:func:`latextify.figures.override._resolve_one`), so a missing figure is
    supplied exactly the way a replacement one is -- ``--figure 3=plot.pdf``, a
    ``--figures-dir`` holding ``fig3.pdf``, page 3 of a ``--figures-pdf``,
    ``figures.yaml``, or ``figures/fig3.pdf`` beside the manuscript.

    Returns a plan whose ``figures`` is the unchanged input when there is
    nothing to do -- no gaps, or no file for any of them.
    """
    docx_path = Path(docx_path)
    sources = sources if sources is not None else OverrideSources()
    stated_to_order, gaps = _stated_to_document_order(docx_path)
    if not gaps:
        return GapFillPlan(figures=figures)

    figures_dir = docx_path.parent / "figures"
    manifest_map = _manifest_for(docx_path, prefix)

    filled: dict[int, tuple[Path, FigureSource]] = {}
    unfilled: list[int] = []
    for number in gaps:
        found = _resolve_one(number, sources, manifest_map, figures_dir, prefix)
        if found is None:
            unfilled.append(number)
        else:
            filled[number] = found

    if not filled:
        return GapFillPlan(figures=figures, unfilled=tuple(gaps))

    # Renumber the extracted figures to the numbers their captions state. This
    # is what re-pairs a shifted caption with its own image.
    by_order = {figure.number: figure for figure in figures}
    renumbering: dict[int, int] = {}
    renumbered: list[Figure] = []
    for stated, order in stated_to_order.items():
        figure = by_order.get(order)
        if figure is None:  # more captions than images beyond the gaps; leave be
            continue
        renumbering[order] = stated
        renumbered.append(_renumber(figure, stated))

    # Any figure no caption claimed (an uncaptioned image, or one in a table)
    # keeps its own number, as long as nothing else took it.
    claimed = set(renumbering)
    taken = set(renumbering.values())
    for figure in figures:
        if figure.number in claimed or figure.number in taken:
            continue
        renumbering[figure.number] = figure.number
        renumbered.append(figure)

    captions = _orphan_captions(docx_path, set(filled))
    for number, (path, source) in filled.items():
        renumbered.append(
            Figure(
                number=number,
                caption=captions.get(number, ""),
                # No image was ever pasted, so there is no embedded file. The
                # supplied path stands as both: `resolved_path` reads the
                # override for a non-EMBEDDED source, and nothing may fall
                # back past it.
                embedded_path=path,
                override_path=path,
                source=source,
            )
        )

    return GapFillPlan(
        figures=tuple(sorted(renumbered, key=lambda f: f.number)),
        renumbering=renumbering,
        filled=tuple(sorted(filled)),
        unfilled=tuple(sorted(unfilled)),
        captions=captions,
    )


def _manifest_for(docx_path: Path, prefix: str) -> dict[int, Path]:
    """The ``figures.yaml`` map beside ``docx_path``; empty for a prefixed set."""
    from latextify.figures.override import MANIFEST_FILENAME, load_manifest

    manifest_path = docx_path.parent / MANIFEST_FILENAME
    if prefix or not manifest_path.is_file():
        return {}
    return load_manifest(manifest_path)


def _renumber(figure: Figure, number: int) -> Figure:
    """``figure`` with a new number; identical when the number is unchanged."""
    from dataclasses import replace

    return figure if figure.number == number else replace(figure, number=number)


def _orphan_captions(docx_path: Path, wanted: set[int]) -> dict[int, str]:
    """The caption TEXT of each wanted gap, read from the manuscript itself.

    Read from the .docx rather than the converted body because the body has
    been through pandoc's LaTeX writer by the time this is used, and the
    caption is what will be typeset -- taking it from the source keeps a
    stray escape out of it.
    """
    import zipfile
    from xml.etree import ElementTree

    from latextify.figures.caption_gaps import _CAPTION_LABEL_RE, _W, _paragraph_text

    try:
        with zipfile.ZipFile(Path(docx_path)) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError):
        return {}

    found: dict[int, str] = {}
    for paragraph in root.iter(f"{_W}p"):
        match = _CAPTION_LABEL_RE.match(_paragraph_text(paragraph))
        if match and int(match.group(1)) in wanted:
            found[int(match.group(1))] = match.group(2).strip()
    return found


#: One orphaned caption paragraph in the converted body: the label pandoc left
#: as ordinary text, alone on its own line. Anchored to line boundaries so a
#: sentence merely MENTIONING "Figure 3" mid-paragraph is never consumed.
def _orphan_paragraph_re(number: int) -> re.Pattern[str]:
    return re.compile(
        r"^(?:Supp(?:lement(?:al|ary)?|l)?\.?\s+)?(?:Figure|Fig\.?)\s*S?\s*"
        rf"{number}\s*[.:]?[^\n]*$",
        re.IGNORECASE | re.MULTILINE,
    )


def apply_to_body(tex: str, plan: GapFillPlan) -> str:
    """Rewrite ``tex`` for ``plan``: renumber anchors, then plant the new ones.

    Both steps happen in a single pass each, so a renumbering that shuffles
    figures past one another (3 -> 4 while 4 -> 5) cannot have an earlier
    substitution clobbered by a later one.
    """
    if not plan.changed:
        return tex

    def renumber(match: re.Match[str]) -> str:
        old = int(match.group(1))
        return f"%%FIGURE:{plan.renumbering.get(old, old)}%%"

    tex = re.sub(r"%%FIGURE:(\d+)%%", renumber, tex)

    for number in plan.filled:
        pattern = _orphan_paragraph_re(number)
        # Only the FIRST match is replaced: the orphan is one paragraph, and a
        # later cross-reference to the same figure must survive as prose.
        tex, count = pattern.subn(f"%%FIGURE:{number}%%", tex, count=1)
        if not count:
            # No orphan paragraph to consume (a caption pandoc swallowed
            # entirely). Falling back to appending would drop the figure
            # somewhere arbitrary, so leave the body alone -- the figure is
            # still written to figures/ and the caller warns.
            continue
    return tex
