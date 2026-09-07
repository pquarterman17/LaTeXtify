"""Figure override resolution: figures.yaml manifest + folder convention (plan items 9, 15).

Implements both non-embedded tiers of the override order documented in
``latextify/figures/__init__.py``:

    1. (plan item 15) an explicit ``figures.yaml`` manifest beside the
       source ``.docx``: ``{<figure-number>: <path>}``. Beats the folder
       convention on conflict -- a number present in the manifest is never
       looked up in ``figures/`` at all.
    2. (plan item 9) a ``figures/`` directory beside the source ``.docx``
       may contain ``fig<N>.<ext>`` files that should be used instead of the
       embedded media for figure ``N``.

When more than one override file exists for the same figure number via the
folder convention (e.g. both ``fig2.pdf`` and ``fig2.png``), extension
priority picks the winner: pdf > eps > svg > png > jpg. The manifest has no
such ambiguity -- each figure number maps to exactly one path.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from latextify.model import Figure, FigureSource

#: Highest-priority extension first. LaTeX prefers vector formats; PDF is
#: the most broadly embeddable vector format under pdflatex/xelatex/Tectonic.
EXTENSION_PRIORITY: tuple[str, ...] = ("pdf", "eps", "svg", "png", "jpg")

#: figures.yaml sidecar filename, expected beside the source .docx.
MANIFEST_FILENAME = "figures.yaml"


class FigureManifestError(ValueError):
    """Raised when a figures.yaml manifest fails schema validation.

    The message always names the offending figure number or field (bad
    number, missing file, non-mapping root) so the error is actionable
    without having to open the file -- same style as
    :class:`latextify.ingest.metadata_guess.MetaValidationError`.
    """


def _manifest_number(raw_key: object, source: str) -> int:
    """Validate and coerce one manifest key to a positive figure number."""
    if isinstance(raw_key, int) and not isinstance(raw_key, bool):
        number = raw_key
    elif isinstance(raw_key, str) and raw_key.strip().lstrip("-").isdigit():
        number = int(raw_key.strip())
    else:
        raise FigureManifestError(f"{source}: figure number {raw_key!r} must be a positive integer")
    if number < 1:
        raise FigureManifestError(f"{source}: figure number {number} must be a positive integer")
    return number


def load_manifest(manifest_path: Path | str) -> dict[int, Path]:
    """Parse and validate a ``figures.yaml`` manifest: ``{<figure-number>: <path>}``.

    Paths are resolved relative to ``manifest_path``'s own directory unless
    already absolute. Every entry is validated eagerly -- bad figure number,
    non-mapping root, missing referenced file -- so a broken manifest fails
    loudly and specifically at resolve time rather than silently falling
    through to the folder/embedded tiers.

    An empty (or ``null``) manifest file is valid and resolves to no entries.
    """
    manifest_path = Path(manifest_path)
    source = manifest_path.name
    try:
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise FigureManifestError(f"{source}: invalid YAML syntax: {exc}") from exc

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise FigureManifestError(f"{source}: root must be a mapping, got {type(data).__name__}")

    resolved: dict[int, Path] = {}
    for raw_number, raw_path in data.items():
        number = _manifest_number(raw_number, source)
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise FigureManifestError(
                f"{source}: figure {number} path must be a non-empty string, got {raw_path!r}"
            )
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = manifest_path.parent / candidate
        if not candidate.is_file():
            raise FigureManifestError(
                f"{source}: figure {number} references a file that does not exist: {candidate}"
            )
        resolved[number] = candidate
    return resolved


@dataclass(frozen=True)
class OverrideSources:
    """Where to look for replacement figure files, beyond the two beside the docx.

    Stage 2 of the vector-figures plan added three ways to supply files without
    copying them next to the manuscript. All three land here rather than
    becoming parallel resolution paths, so there is still exactly one place
    that decides which file wins for a figure.

    Attributes:
        explicit: ``{number: path}`` from ``--figure N=PATH``. The most
            specific thing a user can say, so it beats everything.
        directories: folders holding ``fig<N>.<ext>`` files, searched in
            order. ``--figures-dir`` contributes one; ``--figures-pdf``
            contributes a staging directory of pages split out of a single
            PDF (see :func:`split_figures_pdf`), which is why one mechanism
            covers both.
    """

    explicit: dict[int, Path] = field(default_factory=dict)
    directories: tuple[Path, ...] = ()

    def is_empty(self) -> bool:
        """True when nothing was supplied, i.e. the pre-stage-2 behaviour."""
        return not self.explicit and not self.directories


def split_figures_pdf(pdf_path: Path | str, dest_dir: Path, *, prefix: str = "") -> list[Path]:
    """Write each page of ``pdf_path`` into ``dest_dir`` as ``fig<N>.pdf``.

    Page 1 becomes figure 1, and so on: the convention a user exporting all
    their figures to one PDF already has in mind. The result is an ordinary
    override directory, so the split needs no resolution rules of its own.

    Raises ``ValueError`` naming the file when the PDF cannot be read or holds
    no pages -- the same contract every other ingest boundary uses, so the CLI
    and GUI report it as a clean error rather than a traceback.
    """
    pdf_path = Path(pdf_path)
    try:
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(str(pdf_path))
        pages = list(reader.pages)
    except Exception as exc:
        raise ValueError(f"{pdf_path.name}: could not be read as a PDF ({exc})") from exc
    if not pages:
        raise ValueError(f"{pdf_path.name}: contains no pages")

    dest_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for index, page in enumerate(pages, start=1):
        writer = PdfWriter()
        writer.add_page(page)
        target = dest_dir / f"fig{prefix}{index}.pdf"
        with target.open("wb") as handle:
            writer.write(handle)
        written.append(target)
    return written


def find_override(figures_dir: Path | str, number: int, *, prefix: str = "") -> Path | None:
    """Return the highest-priority ``fig<prefix><number>.<ext>`` file in ``figures_dir``.

    ``prefix`` defaults to ``""`` (the plain ``fig<N>.<ext>`` convention).
    Supplementary-material figures (plan item 21) pass ``prefix="S"`` to look
    for ``figS<N>.<ext>`` instead, so a main-document override and an SI
    override for the same nominal figure number never collide in the same
    ``figures/`` folder.

    Returns ``None`` if ``figures_dir`` doesn't exist or has no matching file
    for ``number`` in any of the recognized extensions.
    """
    figures_dir = Path(figures_dir)
    if not figures_dir.is_dir():
        return None
    for ext in EXTENSION_PRIORITY:
        candidate = figures_dir / f"fig{prefix}{number}.{ext}"
        if candidate.is_file():
            return candidate
    return None


def resolve_overrides(
    figures: tuple[Figure, ...],
    docx_path: Path | str,
    *,
    prefix: str = "",
    sources: OverrideSources | None = None,
) -> tuple[Figure, ...]:
    """Resolve manifest + folder-convention overrides for each figure, beside ``docx_path``.

    Looks for ``figures.yaml`` and a ``figures/`` directory next to
    ``docx_path`` (i.e. ``docx_path.parent``). Resolution order per figure
    number, first match wins:

        1. an explicit ``--figure N=PATH`` entry -> ``FigureSource.EXPLICIT``
        2. a ``fig<N>.<ext>`` file in one of ``sources.directories``
           (``--figures-dir``, or a split ``--figures-pdf``), in the order
           given -> ``FigureSource.OVERRIDE``
        3. an entry in ``figures.yaml`` -> ``FigureSource.MANIFEST``
        4. a ``figures/fig<N>.<ext>`` folder-convention file beside the
           manuscript -> ``FigureSource.OVERRIDE``
        5. unchanged (still ``FigureSource.EMBEDDED``)

    Tiers 1 and 2 are stage 2 of the vector-figures plan; ``sources`` defaults
    to empty, in which case resolution is exactly what it was before -- the
    manifest, then the folder beside the docx.

    A present-but-invalid ``figures.yaml`` raises :class:`FigureManifestError`
    immediately (see :func:`load_manifest`) rather than silently falling
    through to the folder convention -- a broken manifest should never
    resolve as if it were absent.

    ``prefix`` (plan item 21): pass ``"S"`` when resolving a supplementary
    document's figures, so the folder convention looks for
    ``figures/figS<N>.<ext>`` instead of ``figures/fig<N>.<ext>``. The
    ``figures.yaml`` manifest tier is keyed by plain figure number only and
    is skipped entirely for a prefixed figure set -- a main-document
    manifest entry must never silently resolve an SI figure it was never
    meant for.
    """
    docx_path = Path(docx_path)
    sources = sources if sources is not None else OverrideSources()
    figures_dir = docx_path.parent / "figures"
    manifest_path = docx_path.parent / MANIFEST_FILENAME
    manifest_map = load_manifest(manifest_path) if not prefix and manifest_path.is_file() else {}

    resolved: list[Figure] = []
    for figure in figures:
        found = _resolve_one(figure.number, sources, manifest_map, figures_dir, prefix)
        if found is None:
            resolved.append(figure)
        else:
            path, source = found
            resolved.append(replace(figure, override_path=path, source=source))
    return tuple(resolved)


def _resolve_one(
    number: int,
    sources: OverrideSources,
    manifest_map: dict[int, Path],
    figures_dir: Path,
    prefix: str,
) -> tuple[Path, FigureSource] | None:
    """The winning file for one figure number, or ``None`` to keep the embedded one.

    Tier order is documented on :func:`resolve_overrides`. Split out so the
    ordering lives in one readable place rather than inside a loop, and so
    caption-gap filling (:mod:`latextify.figures.gap_fill`) can ask the same
    question for a figure that has no embedded image to fall back to.
    """
    explicit = sources.explicit.get(number) if not prefix else None
    if explicit is not None:
        return explicit, FigureSource.EXPLICIT
    for directory in sources.directories:
        found = find_override(directory, number, prefix=prefix)
        if found is not None:
            return found, FigureSource.OVERRIDE
    manifest_override = manifest_map.get(number)
    if manifest_override is not None:
        return manifest_override, FigureSource.MANIFEST
    found = find_override(figures_dir, number, prefix=prefix)
    if found is not None:
        return found, FigureSource.OVERRIDE
    return None


def describe_source(figure: Figure) -> str:
    """One-line human-readable record of a figure's file provenance.

    Consumed by the consolidated conversion report (plan item 16); exposed
    here so item 9's override test can assert on it directly.
    """
    return f"Figure {figure.number}: source={figure.source.value} ({figure.resolved_path.name})"


def parse_figure_argument(raw: str) -> tuple[int, Path]:
    """Parse one ``--figure N=PATH`` argument into ``(number, path)``.

    Raises ``ValueError`` naming the offending argument for a malformed
    number, a missing ``=``, or a file that is not there -- the same
    fail-loudly-and-specifically contract :func:`load_manifest` uses, so a
    typo is caught before a conversion runs rather than silently ignored.
    """
    number_text, separator, path_text = raw.partition("=")
    if not separator or not path_text.strip():
        raise ValueError(f"--figure {raw!r}: expected NUMBER=PATH (e.g. 3=plots/spectra.pdf)")
    number_text = number_text.strip()
    if not number_text.lstrip("-").isdigit() or int(number_text) < 1:
        raise ValueError(f"--figure {raw!r}: figure number must be a positive integer")
    path = Path(path_text.strip()).expanduser()
    if not path.is_file():
        raise ValueError(f"--figure {raw!r}: no such file: {path}")
    return int(number_text), path


@contextmanager
def build_sources(
    *,
    figures_dir: Path | str | None = None,
    figure_arguments: Sequence[str] = (),
    figures_pdf: Path | str | None = None,
    prefix: str = "",
) -> Iterator[OverrideSources]:
    """Assemble an :class:`OverrideSources` from the three stage-2 CLI options.

    A context manager because ``figures_pdf`` is split into a temporary
    directory of one-page PDFs, which must outlive the conversion but nothing
    more -- yielding keeps that lifetime honest instead of leaving a staging
    directory in the user's tree.

    Raises ``ValueError`` (naming the option) for a directory that is not
    there, a malformed ``--figure`` argument, or an unreadable PDF.
    """
    explicit = dict(parse_figure_argument(raw) for raw in figure_arguments)
    directories: list[Path] = []
    if figures_dir is not None:
        resolved = Path(figures_dir).expanduser()
        if not resolved.is_dir():
            raise ValueError(f"--figures-dir: no such directory: {resolved}")
        directories.append(resolved)

    if figures_pdf is None:
        yield OverrideSources(explicit=explicit, directories=tuple(directories))
        return

    import tempfile

    with tempfile.TemporaryDirectory(prefix="latextify-figures-pdf-") as staging:
        split_figures_pdf(figures_pdf, Path(staging), prefix=prefix)
        # The split pages sit BELOW an explicit --figures-dir: naming one file
        # outright is more specific than "page N of the bundle".
        yield OverrideSources(explicit=explicit, directories=(*directories, Path(staging)))
