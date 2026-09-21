"""Local, content-free diagnostics for offline/workstation troubleshooting."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import pypandoc

from latextify import __version__
from latextify.compile.tectonic import compile_document, find_tectonic
from latextify.figures.vector import _find_metafile_converter


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _check(name: str, action) -> Check:
    try:
        detail = str(action())
    except Exception as exc:
        return Check(name, "fail", f"{type(exc).__name__}: {exc}")
    return Check(name, "pass", detail)


def run_system_check(workdir: Path) -> dict[str, object]:
    """Exercise the local runtime without reading any user manuscript."""
    checks: list[Check] = []
    offline = os.environ.get("LATEXTIFY_OFFLINE", "").lower() in {"1", "true", "yes", "on"}
    checks.append(Check("Network policy", "pass", "offline mode" if offline else "online allowed"))
    checks.append(Check("LaTeXtify", "pass", __version__))
    checks.append(Check("Python", "pass", platform.python_version()))
    checks.append(Check("Operating system", "pass", f"{platform.system()} {platform.machine()}"))

    def writable() -> str:
        workdir.mkdir(parents=True, exist_ok=True)
        probe = workdir / ".latextify-write-check"
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
        return "temporary working folder is writable"

    checks.append(_check("Working folder", writable))

    def pandoc() -> str:
        converted = pypandoc.convert_text(
            "LaTeXtify diagnostic", to="latex", format="markdown", verify_format=False
        )
        if "LaTeXtify diagnostic" not in converted:
            raise RuntimeError("conversion returned unexpected output")
        return f"Pandoc {pypandoc.get_pandoc_version()} converted a test paragraph"

    checks.append(_check("Pandoc", pandoc))

    tectonic = find_tectonic()
    if tectonic is None:
        checks.append(Check("Tectonic", "fail", "PDF engine was not found"))
    else:

        def tectonic_version() -> str:
            completed = subprocess.run(
                [str(tectonic), "--version"],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return completed.stdout.strip() or completed.stderr.strip() or "available"

        checks.append(_check("Tectonic", tectonic_version))
        with tempfile.TemporaryDirectory(prefix="latextify-system-check-") as temporary:
            tex = Path(temporary) / "main.tex"
            tex.write_text(
                "\\documentclass{revtex4-2}\\begin{document}System check\\end{document}\n",
                encoding="utf-8",
            )
            compiled = compile_document(tex, tectonic_path=tectonic, timeout=90)
            if compiled.success and compiled.pdf_path is not None:
                checks.append(Check("Offline PDF compilation", "pass", "test PDF compiled"))
            else:
                detail = compiled.raw_log.strip().splitlines()[-1:] or ["unknown compile error"]
                checks.append(Check("Offline PDF compilation", "fail", detail[0]))

    cache = os.environ.get("TECTONIC_CACHE_DIR")
    cache_path = Path(cache) if cache else None
    if cache_path is not None and cache_path.is_dir():
        checks.append(Check("TeX package cache", "pass", "bundled cache is present"))
    elif offline:
        checks.append(Check("TeX package cache", "fail", "bundled cache directory is missing"))
    else:
        checks.append(Check("TeX package cache", "warn", "using Tectonic's user cache"))

    converter = _find_metafile_converter()
    if converter:
        checks.append(
            Check("EMF/WMF figures", "pass", f"vector conversion via {Path(converter).name}")
        )
    elif sys.platform == "win32":
        checks.append(
            Check(
                "EMF/WMF figures",
                "warn",
                "600-DPI Windows raster fallback; install LibreOffice or Inkscape for vectors",
            )
        )
    else:
        checks.append(
            Check("EMF/WMF figures", "warn", "no LibreOffice or Inkscape converter detected")
        )

    counts = {
        level: sum(item.status == level for item in checks) for level in ("pass", "warn", "fail")
    }
    overall = "fail" if counts["fail"] else "warn" if counts["warn"] else "pass"
    lines = [
        "LaTeXtify system check",
        "========================",
        (
            f"Overall: {overall.upper()} ({counts['pass']} passed, "
            f"{counts['warn']} warnings, {counts['fail']} failed)"
        ),
        "",
    ]
    lines.extend(f"[{item.status.upper()}] {item.name}: {item.detail}" for item in checks)
    lines.extend(
        [
            "",
            "This report contains runtime status only. It does not include manuscript content.",
        ]
    )
    return {
        "overall": overall,
        "checks": [asdict(item) for item in checks],
        "report": "\n".join(lines) + "\n",
    }
