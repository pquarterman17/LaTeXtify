"""Tectonic binary detection/download/cache and compile invocation.

Detection order: `tectonic` on PATH, then the platformdirs cache used by
`download_tectonic`. If neither is present, `ensure_tectonic()` downloads a
**pinned, checksum-verified** Tectonic release for the current platform into
the cache -- never "whatever the latest release happens to be". See the trust
note on the pin constants below: a PATH binary is trusted as user-managed;
a downloaded binary is version- and SHA-256-managed by LaTeXtify.

Compilation runs `tectonic -X compile <main.tex>` with the document's own
directory as the working directory (Tectonic writes the PDF alongside the
source). Vendored journal class/style files (see
`latextify/templates/journals/<name>/vendor/`) are staged into that
directory before compiling, since Tectonic only sees files under its cwd
(plus whatever it can fetch from its bundle/network).
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

import httpx
import platformdirs

from latextify.compile.logs import parse_log
from latextify.model.compile import CompileResult

# A downloaded Tectonic binary is executed, so it is pinned to a reviewed
# version and every release asset is verified against a recorded SHA-256 before
# extraction -- LaTeXtify never runs "whatever GitHub's latest release happens
# to be". Downloading by the pinned tag's direct asset URL (below) also avoids
# the rate-limited releases API entirely, so no GitHub token is needed.
#
# To bump the version: change PINNED_TECTONIC_VERSION, then regenerate every
# asset filename + SHA-256 in _TECTONIC_ASSETS (download each release asset and
# sha256 it). A missing target or a checksum mismatch fails closed.
PINNED_TECTONIC_VERSION = "0.16.9"
_RELEASE_DOWNLOAD_BASE = (
    "https://github.com/tectonic-typesetting/tectonic/releases/download/"
    f"tectonic@{PINNED_TECTONIC_VERSION}"
)
#: target triple -> (release asset filename, expected SHA-256 of that asset).
_TECTONIC_ASSETS: dict[str, tuple[str, str]] = {
    "x86_64-unknown-linux-gnu": (
        "tectonic-0.16.9-x86_64-unknown-linux-gnu.tar.gz",
        "f3c825128095dc3399ea11c08c18035b33050a216930c295c79e8eb11bd21de4",
    ),
    "x86_64-pc-windows-msvc": (
        "tectonic-0.16.9-x86_64-pc-windows-msvc.zip",
        "131a24604785a9600989a3d91225f597df52ac06f00aeffe86fd529f99ee5cdd",
    ),
    "x86_64-apple-darwin": (
        "tectonic-0.16.9-x86_64-apple-darwin.tar.gz",
        "79d8839fa3594bfea9b2bf2ac0a0455bcc4d0de956a5e5c403107e9a72f79e86",
    ),
    "aarch64-apple-darwin": (
        "tectonic-0.16.9-aarch64-apple-darwin.tar.gz",
        "edb67c61aba768289f6da441c9e6f523cfaff4f8b2a5708523ef29c543f8e88e",
    ),
}
#: Refuse a download/archive larger than this; the biggest real asset is ~22 MB.
_MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
_DOWNLOAD_CHUNK = 1 << 20  # 1 MiB
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


def _asset_for_triple(triple: str) -> tuple[str, str]:
    """Return the pinned ``(asset_filename, sha256)`` for a target, or fail closed."""
    asset = _TECTONIC_ASSETS.get(triple)
    if asset is None:
        raise TectonicNotAvailableError(
            f"No pinned Tectonic {PINNED_TECTONIC_VERSION} asset for target '{triple}'. "
            f"Put a 'tectonic' binary on PATH, or add the target to _TECTONIC_ASSETS."
        )
    return asset


def _download_verified_archive(
    url: str,
    expected_sha256: str,
    dest_path: Path,
    *,
    client: httpx.Client,
    max_bytes: int = _MAX_ARCHIVE_BYTES,
) -> None:
    """Stream ``url`` to ``dest_path``, enforcing a size cap and SHA-256.

    Streams in bounded chunks (never holds the whole archive in memory), aborts
    if the response exceeds ``max_bytes``, and raises before the caller extracts
    anything if the computed digest does not match ``expected_sha256``.
    """
    digest = hashlib.sha256()
    total = 0
    try:
        with client.stream("GET", url, headers={"User-Agent": _USER_AGENT}) as resp:
            resp.raise_for_status()
            with dest_path.open("wb") as out:
                for chunk in resp.iter_bytes(_DOWNLOAD_CHUNK):
                    total += len(chunk)
                    if total > max_bytes:
                        raise TectonicNotAvailableError(
                            f"Tectonic download from {url} exceeded {max_bytes} bytes; refusing."
                        )
                    digest.update(chunk)
                    out.write(chunk)
    except httpx.HTTPError as exc:
        raise TectonicNotAvailableError(
            f"Could not download Tectonic from {url}: {exc}"
        ) from exc
    actual = digest.hexdigest()
    if actual != expected_sha256:
        raise TectonicNotAvailableError(
            f"Tectonic archive checksum mismatch for {url}: "
            f"expected {expected_sha256}, got {actual}"
        )


def _is_root_binary(member_name: str, binary_name: str) -> bool:
    """True only for a root-level member named exactly ``binary_name``.

    An optional leading ``./`` is tolerated; anything with a real path segment
    (``dir/tectonic``, ``../tectonic``) is rejected, so a nested or
    parent-traversing member never matches.
    """
    normalized = member_name[2:] if member_name.startswith("./") else member_name
    return normalized == binary_name


def _single_binary_member(matches: list, binary_name: str, archive_name: str):
    """Return the sole matching member, or fail closed on none/duplicates."""
    if not matches:
        raise TectonicNotAvailableError(
            f"Tectonic archive '{archive_name}' has no root-level {binary_name} member"
        )
    if len(matches) > 1:
        raise TectonicNotAvailableError(
            f"Tectonic archive '{archive_name}' has multiple {binary_name} members"
        )
    return matches[0]


def _read_zip_binary(archive_path: Path, binary_name: str, archive_name: str) -> bytes:
    with zipfile.ZipFile(archive_path) as zf:
        matches = [i for i in zf.infolist() if _is_root_binary(i.filename, binary_name)]
        info = _single_binary_member(matches, binary_name, archive_name)
        if info.is_dir():
            raise TectonicNotAvailableError(
                f"Tectonic archive member '{info.filename}' is a directory, not a file"
            )
        # A zip symlink stores its Unix mode in the high 16 bits of external_attr.
        if stat.S_ISLNK(info.external_attr >> 16):
            raise TectonicNotAvailableError(
                f"Tectonic archive member '{info.filename}' is a symlink"
            )
        return zf.read(info)


def _read_tar_binary(archive_path: Path, binary_name: str, archive_name: str) -> bytes:
    with tarfile.open(archive_path, mode="r:gz") as tf:
        matches = [m for m in tf.getmembers() if _is_root_binary(m.name, binary_name)]
        member = _single_binary_member(matches, binary_name, archive_name)
        # isfile() is True only for a regular file -- rejects symlink, hardlink,
        # directory, device, and fifo members in one check.
        if not member.isfile():
            raise TectonicNotAvailableError(
                f"Tectonic archive member '{member.name}' is not a regular file"
            )
        extracted = tf.extractfile(member)
        if extracted is None:
            raise TectonicNotAvailableError(
                f"Could not read '{member.name}' from Tectonic archive '{archive_name}'"
            )
        return extracted.read()


def _safe_extract_binary(
    archive_path: Path, archive_name: str, dest_dir: Path, binary_name: str
) -> Path:
    """Extract ONLY the one expected root-level binary member into ``dest_dir``.

    Never uses ``extractall``: it reads exactly the ``binary_name`` member
    (rejecting missing/duplicate/link/directory/traversal members), writes it to
    a temp sibling, sets the exec bit on POSIX targets, then atomically replaces
    the cache entry -- so an interrupted extract can never leave a runnable
    partial binary or clobber a good cached one until the new binary is complete.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    if archive_name.endswith(".zip"):
        data = _read_zip_binary(archive_path, binary_name, archive_name)
    else:
        data = _read_tar_binary(archive_path, binary_name, archive_name)

    partial = dest_dir / f".{binary_name}.partial"
    partial.write_bytes(data)
    if not binary_name.endswith(".exe"):
        mode = partial.stat().st_mode
        partial.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    dest = dest_dir / binary_name
    os.replace(partial, dest)  # atomic swap of the cache entry
    return dest


def download_tectonic_release(
    triple: str,
    binary_name: str,
    dest_dir: Path,
    *,
    client: httpx.Client | None = None,
) -> Path:
    """Download the PINNED Tectonic release binary for an EXPLICIT target.

    ``triple`` selects the pinned release asset (e.g. ``x86_64-unknown-linux-gnu``)
    and ``binary_name`` is the extracted executable's name for that target
    (``tectonic`` or ``tectonic.exe``); the verified binary lands in
    ``dest_dir``. This is the cross-platform primitive the offline-kit builder
    uses to fetch a binary for a target that is NOT the build host;
    :func:`download_tectonic` is the current-platform, cache-directed wrapper.

    The asset is downloaded by its direct pinned-tag URL into a private temp
    file, its SHA-256 is verified against :data:`_TECTONIC_ASSETS`, and only the
    single expected binary member is extracted -- so a substituted or corrupted
    download fails closed without writing outside the temp dir or replacing a
    valid cached binary. Always hits the network (no idempotence check -- the
    caller owns the destination policy).
    """
    asset_name, expected_sha = _asset_for_triple(triple)
    url = f"{_RELEASE_DOWNLOAD_BASE}/{asset_name}"
    owns_client = client is None
    http_client = client or httpx.Client(follow_redirects=True, timeout=120.0)
    try:
        with tempfile.TemporaryDirectory(prefix="latextify-tectonic-") as tmp:
            archive_path = Path(tmp) / asset_name
            _download_verified_archive(url, expected_sha, archive_path, client=http_client)
            return _safe_extract_binary(archive_path, asset_name, dest_dir, binary_name)
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
