"""Pre-warming the TeX bundle cache that ships inside an offline kit.

Split out of :mod:`latextify.kit.build` (2026-08-10). A kit is useless on an
air-gapped machine if the first compile still wants to download TeX packages,
so the builder compiles a probe document per journal on the CONNECTED build
host and ships the resulting cache.

Two wrinkles this module exists to hold: the cache carries host/arch-specific
engine format dumps that must NOT ride along to another platform (a target
regenerates them locally, offline), and Windows' 260-character path limit is
reachable inside a deep cache tree, so paths are prefixed for the copy.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from latextify.templates import loader

#: Cache subtrees that are host/arch-specific engine format dumps (not TeX
#: source) -- a target regenerates these locally and offline from the cached
#: sources, so shipping them would only bloat the kit and risk a cross-arch
#: mismatch. Stripped before the warmed cache is copied into the kit.
NONPORTABLE_CACHE_NAMES = ("formats",)

# The warm-up document body. Fonts are pulled by SIZE x WEIGHT x FAMILY, not by
# a document's metadata, so a trivial body under-warms: a real manuscript's 9pt
# abstract needs lmroman9, its 12pt-bold headings need lmroman12-bold -- neither
# is loaded by "Warm-up." at 10pt regular. Latin Modern ships discrete design
# sizes (5,6,7,8,9,10,12,17pt), each a separate font file per weight/family, and
# a class picks the nearest for any requested size. So \WarmAt renders every
# design size (plus the common in-between sizes classes ask for) in roman
# regular/bold/italic/bold-italic, small-caps, sans, and typewriter, and the
# body adds inline + display math (math font). Journal-agnostic (only
# \section/text/math/table -- every registered class is article-derived), this
# covers the font files an actual compile of any journal pulls.
WARM_BODY = r"""
\begin{document}
\section{Cache warm-up}
\newcommand\WarmAt[1]{{\fontsize{#1}{#1}\selectfont
  reg \textbf{bold} \textit{italic} \textbf{\textit{bolditalic}} \textsc{smallcaps}
  \textsf{sans} \texttt{mono}}\par}
\WarmAt{5}\WarmAt{6}\WarmAt{7}\WarmAt{8}\WarmAt{9}\WarmAt{10}\WarmAt{10.5}%
\WarmAt{11}\WarmAt{12}\WarmAt{14}\WarmAt{17}\WarmAt{20}\WarmAt{24}
Inline math $E = mc^2$ and a display equation:
\[ \int_0^1 x^2 \, \mathrm{d}x = \tfrac{1}{3}, \qquad \alpha\beta\gamma\sum_{n=1}^{\infty}. \]
\begin{table}[htbp]
\centering
\caption{Warm-up table.}
\begin{tabular}{ll}
\toprule
Left & Right \\
\midrule
one & two \\
\bottomrule
\end{tabular}
\end{table}
\end{document}
"""


def long_path(p: Path) -> str:
    """Windows extended-length (\\\\?\\) form so deep cache paths clear MAX_PATH.

    Tectonic caches files under 64-char content-hash names beneath
    ``bundles/data/``; a moderately deep kit output dir can push those past the
    260-char legacy limit. The ``\\\\?\\`` prefix opts a path out of that limit.
    No-op off Windows / for already-prefixed paths.
    """
    s = str(p)
    if os.name == "nt" and not s.startswith("\\\\?\\"):
        return "\\\\?\\" + s
    return s


def copy_portable_cache(src: Path, dest: Path) -> None:
    """Copy the warmed Tectonic cache into the kit, minus non-portable formats."""
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        long_path(src.resolve()),
        long_path(dest.resolve()),
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(*NONPORTABLE_CACHE_NAMES, "*.fmt"),
    )


def warm_tex_cache(tex_cache: Path, journals: list[str]) -> list[str]:
    """Prime ``tex_cache`` with each journal's TeX packages, using the HOST Tectonic.

    Compiles a minimal document per journal (its rendered preamble + a trivial
    body) so the exact packages each journal's class/preamble pulls land in the
    cache. Warming runs against a SHORT-path temp cache -- Tectonic's format-file
    generation can exceed Windows MAX_PATH under a deep kit output directory --
    then the TeX *source* files are copied into the kit (the host-specific engine
    format dump is dropped; the target regenerates it locally, offline, from the
    sources, which is what makes the cache cross-platform). Per-journal failure
    is a warning, not fatal. Returns the journals that warmed successfully.
    """
    import tempfile

    from latextify.compile.tectonic import compile_document, ensure_tectonic

    tex_cache.mkdir(parents=True, exist_ok=True)
    host_tectonic = ensure_tectonic()  # host binary, only to POPULATE the cache
    warmed: list[str] = []
    prev = os.environ.get("TECTONIC_CACHE_DIR")
    with tempfile.TemporaryDirectory(prefix="ltx-warm-") as tmp:
        work_cache = Path(tmp) / "c"
        work_cache.mkdir()
        os.environ["TECTONIC_CACHE_DIR"] = str(work_cache)
        try:
            for name in journals:
                try:
                    journal = loader.load(name)
                    preamble = journal.render_preamble()
                except Exception as exc:  # noqa: BLE001 - a bad journal must not kill the build
                    print(f"  ! skip warming {name}: {exc}", flush=True)
                    continue
                workdir = Path(tmp) / f"j_{name}"
                workdir.mkdir(parents=True, exist_ok=True)
                tex = workdir / "warm.tex"
                tex.write_text(preamble + WARM_BODY, encoding="utf-8")
                vendor = journal.root / "vendor"
                vendor_dir = vendor if vendor.is_dir() else None
                # A cold cache downloads the whole TeX bundle on the first
                # compile; a transient blip there is worth one retry.
                result = compile_document(tex, tectonic_path=host_tectonic, vendor_dir=vendor_dir)
                if not result.success:
                    result = compile_document(
                        tex, tectonic_path=host_tectonic, vendor_dir=vendor_dir
                    )
                if result.success:
                    warmed.append(name)
                    print(f"  warmed {name}", flush=True)
                else:
                    tail = "\n".join(result.raw_log.splitlines()[-8:])
                    print(
                        f"  ! warming {name} did not compile clean (packages may be "
                        f"partially cached); continuing.\n    log tail:\n{tail}",
                        flush=True,
                    )
        finally:
            if prev is None:
                os.environ.pop("TECTONIC_CACHE_DIR", None)
            else:
                os.environ["TECTONIC_CACHE_DIR"] = prev
        if warmed:
            copy_portable_cache(work_cache, tex_cache)
    return warmed
