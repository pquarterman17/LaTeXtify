r"""Journal template registry: discover, validate, and render journal folders.

A journal is a folder ``journals/<name>/`` holding a ``manifest.yaml`` plus two
Jinja templates (``preamble.tex.j2``, ``metadata.tex.j2``). This module turns
that folder into a validated :class:`Journal` object and renders LaTeX from it.

Public surface
--------------
    load(name, *, journals_dir=None) -> Journal     validated journal object
    available(*, journals_dir=None) -> list[str]     discovered journal names
    discover(*, journals_dir=None) -> dict[str, Path]  name -> manifest path
    ManifestError                                    raised on any bad manifest

Manifest schema (``manifest.yaml``)
-----------------------------------
    class: str                       # REQUIRED  \documentclass{<class>}
    class_options: [str]             # optional  \documentclass[opt,opt]{...}
    packages:                        # optional  \usepackage[...]{name}
      - name: str                    #   REQUIRED per entry
        options: [str]               #   optional
    bib:                             # REQUIRED
      default_mode: str              #   REQUIRED, must be a key of `modes`
      modes:                         #   REQUIRED, >= 1 entry
        <mode-name>:                 #   mode name is "numeric" or "authoryear"
          bibstyle: str              #     REQUIRED  \bibliographystyle{<bibstyle>}
          natbib_options: [str]      #     optional  natbib package options
    metadata_scheme: str             # REQUIRED  informal name the emitter maps
    figure_env:                      # optional
      single: str                    #   default "figure"
      wide: str                      #   default "figure*"  (two-column spans)
    vendor: [str]                    # optional  class/style files to stage

Every validation failure raises :class:`ManifestError` naming both the offending
field and the journal, e.g. ``revtex4-2: bib.modes must define at least one mode``.

Jinja delimiters are remapped (``\VAR{x}`` for variables, ``%% for ...`` line
statements) so LaTeX braces pass through untouched — see :data:`_JINJA_KW`.
Templates render from the :class:`~latextify.model.meta.Meta` IR (metadata) and
the manifest config (preamble); nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from latextify.model.meta import Meta
from latextify.templates.authors import group_consecutive_by_affiliation

# Journal folders shipped inside the package.
_DEFAULT_JOURNALS_DIR = Path(__file__).resolve().parent / "journals"

# Citation modes a manifest is allowed to declare.
_KNOWN_MODES = ("numeric", "authoryear")

# Jinja environment tuned for LaTeX: literal braces, ``\VAR{}`` variables and
# ``%% ...`` line statements (which read as LaTeX comments in the raw template).
_JINJA_KW: dict[str, Any] = dict(
    block_start_string=r"\BLOCK{",
    block_end_string="}",
    variable_start_string=r"\VAR{",
    variable_end_string="}",
    comment_start_string=r"\#{",
    comment_end_string="}",
    line_statement_prefix="%%",
    line_comment_prefix="%#",
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
    autoescape=False,
    undefined=StrictUndefined,
)


class ManifestError(ValueError):
    """A journal manifest is missing, malformed, or violates the schema.

    The message always names the offending field and the journal so a failing
    manifest is fixable without reading this module.
    """


# --------------------------------------------------------------------------- #
# Parsed manifest dataclasses (frozen; the validated, in-memory schema)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Package:
    """One ``\\usepackage[options]{name}`` line."""

    name: str
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class BibMode:
    """One citation mode (numeric / authoryear) and the bibstyle it selects."""

    name: str
    bibstyle: str
    natbib_options: tuple[str, ...] = ()


@dataclass(frozen=True)
class FigureEnv:
    """Figure float environments: ``single`` column vs ``wide`` (spanning)."""

    single: str = "figure"
    wide: str = "figure*"


@dataclass(frozen=True)
class Journal:
    """A validated journal template, ready to render preamble + metadata."""

    name: str
    root: Path
    document_class: str
    class_options: tuple[str, ...]
    packages: tuple[Package, ...]
    bib_modes: dict[str, BibMode]
    default_mode: str
    metadata_scheme: str
    figure_env: FigureEnv
    vendor: tuple[str, ...]

    # -- rendering ------------------------------------------------------- #

    def _env(self) -> Environment:
        env = Environment(loader=FileSystemLoader(str(self.root)), **_JINJA_KW)
        # Grouping strategies available to every journal's metadata template.
        env.globals["group_authors"] = group_consecutive_by_affiliation
        return env

    def resolve_mode(self, mode: str | None) -> BibMode:
        """Return the :class:`BibMode` for ``mode`` (or the default).

        Raises :class:`ManifestError` naming the allowed modes if unsupported —
        this is the check plan item 18's ``--citation-style`` flag reuses.
        """
        chosen = mode or self.default_mode
        if chosen not in self.bib_modes:
            allowed = ", ".join(sorted(self.bib_modes))
            raise ManifestError(
                f"{self.name}: citation mode {chosen!r} not supported "
                f"(allowed: {allowed})"
            )
        return self.bib_modes[chosen]

    def render_preamble(self, *, mode: str | None = None) -> str:
        """Render ``preamble.tex`` for the selected citation mode."""
        bib = self.resolve_mode(mode)
        template = self._env().get_template("preamble.tex.j2")
        return template.render(
            document_class=self.document_class,
            class_options=list(self.class_options),
            packages=self.packages,
            bibstyle=bib.bibstyle,
            natbib_options=list(bib.natbib_options),
            figure_env=self.figure_env,
            metadata_scheme=self.metadata_scheme,
        )

    def render_metadata(self, meta: Meta) -> str:
        """Render ``metadata.tex`` (title/author/affiliation block) from ``meta``."""
        template = self._env().get_template("metadata.tex.j2")
        return template.render(meta=meta)


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def _journals_dir(journals_dir: Path | None) -> Path:
    return Path(journals_dir) if journals_dir is not None else _DEFAULT_JOURNALS_DIR


def discover(*, journals_dir: Path | None = None) -> dict[str, Path]:
    """Map each journal name to its ``manifest.yaml`` path.

    A journal is any immediate subdirectory of ``journals/`` that contains a
    ``manifest.yaml``. Directories without one are ignored (not journals yet).
    """
    root = _journals_dir(journals_dir)
    found: dict[str, Path] = {}
    if not root.is_dir():
        return found
    for child in sorted(root.iterdir()):
        manifest = child / "manifest.yaml"
        if child.is_dir() and manifest.is_file():
            found[child.name] = manifest
    return found


def available(*, journals_dir: Path | None = None) -> list[str]:
    """Sorted list of discovered journal names."""
    return sorted(discover(journals_dir=journals_dir))


# --------------------------------------------------------------------------- #
# Loading + validation
# --------------------------------------------------------------------------- #


def load(name: str, *, journals_dir: Path | None = None) -> Journal:
    """Load and validate the journal named ``name``.

    Raises :class:`ManifestError` if the journal is unknown, its manifest is
    unreadable, or the manifest violates the schema (message names the field).
    """
    manifests = discover(journals_dir=journals_dir)
    if name not in manifests:
        known = ", ".join(sorted(manifests)) or "(none)"
        raise ManifestError(f"{name}: no such journal (known journals: {known})")

    manifest_path = manifests[name]
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ManifestError(f"{name}: manifest is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ManifestError(
            f"{name}: manifest must be a YAML mapping, got {_typename(raw)}"
        )

    return _build_journal(name, manifest_path.parent, raw)


def _build_journal(name: str, root: Path, data: dict[str, Any]) -> Journal:
    """Validate a raw manifest mapping and build a :class:`Journal`."""
    document_class = _require_str(data, "class", name)
    class_options = _opt_str_list(data, "class_options", name)
    packages = _parse_packages(data.get("packages"), name)
    bib_modes, default_mode = _parse_bib(data.get("bib"), name)
    metadata_scheme = _require_str(data, "metadata_scheme", name)
    figure_env = _parse_figure_env(data.get("figure_env"), name)
    vendor = _opt_str_list(data, "vendor", name)

    return Journal(
        name=name,
        root=root,
        document_class=document_class,
        class_options=class_options,
        packages=packages,
        bib_modes=bib_modes,
        default_mode=default_mode,
        metadata_scheme=metadata_scheme,
        figure_env=figure_env,
        vendor=vendor,
    )


# --------------------------------------------------------------------------- #
# Field-level validation helpers (each raises a field-naming ManifestError)
# --------------------------------------------------------------------------- #


def _typename(value: Any) -> str:
    return type(value).__name__


def _require_str(data: dict[str, Any], key: str, journal: str) -> str:
    if key not in data:
        raise ManifestError(f"{journal}: manifest missing required key {key!r}")
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(
            f"{journal}: manifest key {key!r} must be a non-empty string, "
            f"got {_typename(value)}"
        )
    return value


def _opt_str_list(data: dict[str, Any], key: str, journal: str) -> tuple[str, ...]:
    if key not in data or data[key] is None:
        return ()
    value = data[key]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ManifestError(
            f"{journal}: manifest key {key!r} must be a list of strings"
        )
    return tuple(value)


def _parse_packages(value: Any, journal: str) -> tuple[Package, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ManifestError(f"{journal}: 'packages' must be a list")
    packages: list[Package] = []
    for i, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ManifestError(
                f"{journal}: packages[{i}] must be a mapping with a 'name' key"
            )
        pkg_name = entry.get("name")
        if not isinstance(pkg_name, str) or not pkg_name.strip():
            raise ManifestError(
                f"{journal}: packages[{i}] missing required non-empty 'name'"
            )
        opts = entry.get("options")
        if opts is None:
            options: tuple[str, ...] = ()
        elif isinstance(opts, list) and all(isinstance(o, str) for o in opts):
            options = tuple(opts)
        else:
            raise ManifestError(
                f"{journal}: packages[{i}].options must be a list of strings"
            )
        packages.append(Package(name=pkg_name, options=options))
    return tuple(packages)


def _parse_bib(value: Any, journal: str) -> tuple[dict[str, BibMode], str]:
    if value is None:
        raise ManifestError(f"{journal}: manifest missing required key 'bib'")
    if not isinstance(value, dict):
        raise ManifestError(f"{journal}: 'bib' must be a mapping")

    modes_raw = value.get("modes")
    if not isinstance(modes_raw, dict) or not modes_raw:
        raise ManifestError(f"{journal}: bib.modes must define at least one mode")

    modes: dict[str, BibMode] = {}
    for mode_name, spec in modes_raw.items():
        if mode_name not in _KNOWN_MODES:
            allowed = ", ".join(_KNOWN_MODES)
            raise ManifestError(
                f"{journal}: bib.modes has unknown mode {mode_name!r} "
                f"(allowed: {allowed})"
            )
        if not isinstance(spec, dict):
            raise ManifestError(
                f"{journal}: bib.modes.{mode_name} must be a mapping"
            )
        bibstyle = spec.get("bibstyle")
        if not isinstance(bibstyle, str) or not bibstyle.strip():
            raise ManifestError(
                f"{journal}: bib.modes.{mode_name} missing required non-empty "
                f"'bibstyle'"
            )
        nat = spec.get("natbib_options")
        if nat is None:
            natbib_options: tuple[str, ...] = ()
        elif isinstance(nat, list) and all(isinstance(o, str) for o in nat):
            natbib_options = tuple(nat)
        else:
            raise ManifestError(
                f"{journal}: bib.modes.{mode_name}.natbib_options must be a "
                f"list of strings"
            )
        modes[mode_name] = BibMode(
            name=mode_name, bibstyle=bibstyle, natbib_options=natbib_options
        )

    default_mode = value.get("default_mode")
    if not isinstance(default_mode, str):
        raise ManifestError(
            f"{journal}: bib.default_mode is required and must be a string"
        )
    if default_mode not in modes:
        allowed = ", ".join(sorted(modes))
        raise ManifestError(
            f"{journal}: bib.default_mode {default_mode!r} is not among defined "
            f"modes ({allowed})"
        )
    return modes, default_mode


def _parse_figure_env(value: Any, journal: str) -> FigureEnv:
    if value is None:
        return FigureEnv()
    if not isinstance(value, dict):
        raise ManifestError(f"{journal}: 'figure_env' must be a mapping")
    single = value.get("single", "figure")
    wide = value.get("wide", "figure*")
    if not isinstance(single, str) or not isinstance(wide, str):
        raise ManifestError(
            f"{journal}: figure_env.single and figure_env.wide must be strings"
        )
    return FigureEnv(single=single, wide=wide)
