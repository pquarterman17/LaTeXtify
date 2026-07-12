"""Tectonic binary detection/download/cache and compile invocation.

Detection order: `tectonic` on PATH, then the platformdirs cache used by
`download_tectonic`. If neither is present, `ensure_tectonic()` downloads
the latest GitHub release for the current platform into the cache.

Compilation runs `tectonic -X compile <main.tex>` with the document's own
directory as the working directory (Tectonic writes the PDF alongside the
source). Vendored journal class/style files (see
`latextify/templates/journals/<name>/vendor/`) are staged into that
directory before compiling, since Tectonic only sees files under its cwd
(plus whatever it can fetch from its bundle/network).
"""

from __future__ import annotations

import io
import os
import platform
import shutil
import stat
import subprocess
import tarfile
import zipfile
from pathlib import Path

import httpx
import platformdirs

from latextify.compile.logs import parse_log
from latextify.model.compile import CompileResult

GITHUB_LATEST_RELEASE_API = (
    "https://api.github.com/repos/tectonic-typesetting/tectonic/releases/latest"
)
_CACHE_APP_NAME = "latextify"
_CACHE_APP_AUTHOR = "latextify"
_USER_AGENT = "latextify (+https://github.com/latextify)"

# Default compile timeout: package downloads on a cold Tectonic cache can be slow.
DEFAULT_COMPILE_TIMEOUT = 300.0


class TectonicNotAvailableError(RuntimeError):
    """Raised when tectonic cannot be located on PATH or downloaded/cached."""


def cache_dir() -> Path:
    """Directory Tectonic binaries are downloaded into (platformdirs user cache)."""
    return Path(platformdirs.user_cache_dir(_CACHE_APP_NAME, _CACHE_APP_AUTHOR)) / "tectonic"


def _binary_name() -> str:
    return "tectonic.exe" if platform.system() == "Windows" else "tectonic"


def find_tectonic() -> Path | None:
    """Look for `tectonic` on PATH first, then in the local download cache.

    Returns None if not found anywhere -- caller should fall back to
    `download_tectonic` / `ensure_tectonic`.
    """
    on_path = shutil.which("tectonic")
    if on_path:
        return Path(on_path)
    cached = cache_dir() / _binary_name()
    if cached.is_file():
        return cached
    return None


def _target_triple() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Windows":
        return "x86_64-pc-windows-msvc"
    if system == "Darwin":
        return "aarch64-apple-darwin" if machine in ("arm64", "aarch64") else "x86_64-apple-darwin"
    if system == "Linux":
        return "x86_64-unknown-linux-gnu"
    raise TectonicNotAvailableError(f"Unsupported platform for Tectonic download: {system}")


def _pick_asset(assets: list[dict], triple: str) -> dict:
    for asset in assets:
        name = asset.get("name", "")
        if triple in name and (name.endswith(".zip") or name.endswith(".tar.gz")):
            return asset
    raise TectonicNotAvailableError(
        f"No Tectonic release asset found for platform target '{triple}'"
    )


def _extract_tectonic_binary(
    archive_bytes: bytes, archive_name: str, dest_dir: Path, binary_name: str
) -> Path:
    """Extract ``binary_name`` from a downloaded Tectonic archive into ``dest_dir``.

    Tectonic release archives carry the binary at their root, so a plain
    ``extractall`` lands ``tectonic``/``tectonic.exe`` directly in ``dest_dir``.
    A POSIX target binary is made executable (harmless/irrelevant for a Windows
    ``.exe``, which is identified by the name, not the build host).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    if archive_name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            zf.extractall(dest_dir)
    else:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tf:
            tf.extractall(dest_dir)

    dest = dest_dir / binary_name
    if not dest.is_file():
        raise TectonicNotAvailableError(
            f"Downloaded Tectonic archive '{archive_name}' did not contain {binary_name}"
        )
    if not binary_name.endswith(".exe"):
        mode = dest.stat().st_mode
        dest.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return dest


def download_tectonic_release(
    triple: str,
    binary_name: str,
    dest_dir: Path,
    *,
    client: httpx.Client | None = None,
) -> Path:
    """Download the latest Tectonic release binary for an EXPLICIT target.

    ``triple`` selects the release asset (e.g. ``x86_64-unknown-linux-gnu``)
    and ``binary_name`` is the extracted executable's name for that target
    (``tectonic`` or ``tectonic.exe``); the binary lands in ``dest_dir``. This
    is the cross-platform primitive the offline-kit builder uses to fetch a
    binary for a target that is NOT the build host; :func:`download_tectonic`
    is the current-platform, cache-directed wrapper around it. Always hits the
    network (no idempotence check -- the caller owns the destination policy).
    """
    owns_client = client is None
    http_client = client or httpx.Client(follow_redirects=True, timeout=120.0)
    try:
        headers = {"User-Agent": _USER_AGENT}
        # Anonymous GitHub API calls are aggressively rate-limited from shared
        # IPs (CI runners). Use a token when the environment provides one.
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            resp = http_client.get(GITHUB_LATEST_RELEASE_API, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise TectonicNotAvailableError(
                f"Could not query the Tectonic release API: {exc}"
            ) from exc
        release = resp.json()

        asset = _pick_asset(release.get("assets", []), triple)
        archive_resp = http_client.get(
            asset["browser_download_url"], headers={"User-Agent": _USER_AGENT}
        )
        archive_resp.raise_for_status()
        return _extract_tectonic_binary(
            archive_resp.content, asset["name"], dest_dir, binary_name
        )
    finally:
        if owns_client:
            http_client.close()


def download_tectonic(*, client: httpx.Client | None = None, force: bool = False) -> Path:
    """Download the latest Tectonic release for this platform into the cache dir.

    Idempotent: if the cached binary already exists and `force` is False,
    returns it without hitting the network. Returns the path to the cached
    executable.
    """
    dest = cache_dir() / _binary_name()
    if dest.is_file() and not force:
        return dest
    return download_tectonic_release(
        _target_triple(), _binary_name(), cache_dir(), client=client
    )


def ensure_tectonic() -> Path:
    """Return a usable Tectonic executable path, downloading it if necessary."""
    found = find_tectonic()
    if found is not None:
        return found
    return download_tectonic()


def stage_vendor_files(vendor_dir: Path, workdir: Path) -> list[Path]:
    """Copy vendored class/style files (flat, non-recursive) into `workdir`.

    Used to make journal `.cls`/`.bst`/`.sty` files that are missing from
    Tectonic's bundle visible to the compile. No-op if `vendor_dir` doesn't
    exist. Returns the list of staged destination paths.
    """
    if not vendor_dir.is_dir():
        return []
    staged: list[Path] = []
    for item in sorted(vendor_dir.iterdir()):
        if item.is_file():
            dest = workdir / item.name
            shutil.copy2(item, dest)
            staged.append(dest)
    return staged


def compile_document(
    tex_path: Path,
    *,
    tectonic_path: Path | None = None,
    vendor_dir: Path | None = None,
    timeout: float = DEFAULT_COMPILE_TIMEOUT,
) -> CompileResult:
    """Compile `tex_path` with Tectonic.

    The document's own directory is used as the working directory (Tectonic
    resolves relative `\\input`/`\\bibliography`/figure paths and writes the
    PDF there). If `vendor_dir` is given, its files are copied into that
    working directory first (see `stage_vendor_files`).

    Never raises on a TeX compile failure -- that's reported via
    `CompileResult.success` / `.diagnostics`. Raises `TectonicNotAvailableError`
    if the binary can't be found/downloaded, and lets `subprocess.TimeoutExpired`
    propagate for a hung compile.
    """
    binary = tectonic_path or ensure_tectonic()
    workdir = tex_path.parent

    if vendor_dir is not None:
        stage_vendor_files(vendor_dir, workdir)

    proc = subprocess.run(
        [str(binary), "-X", "compile", tex_path.name, "--keep-logs"],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    raw_log = proc.stdout + proc.stderr
    log_path = workdir / f"{tex_path.stem}.log"
    if log_path.is_file():
        raw_log += "\n" + log_path.read_text(encoding="utf-8", errors="replace")

    diagnostics = parse_log(raw_log, default_file=tex_path.name)
    pdf_path = workdir / f"{tex_path.stem}.pdf"
    pdf_exists = pdf_path.is_file()

    return CompileResult(
        success=proc.returncode == 0 and pdf_exists,
        pdf_path=pdf_path if pdf_exists else None,
        diagnostics=tuple(diagnostics),
        raw_log=raw_log,
        returncode=proc.returncode,
    )
