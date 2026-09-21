from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from latextify.compile import tectonic
from latextify.portable import application_dir, choose_port, configure_offline_environment


def test_portable_environment_uses_bundled_runtime(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "tectonic").mkdir()
    (tmp_path / "tex-bundle-cache").mkdir()
    monkeypatch.setenv("PATH", "existing-path")
    monkeypatch.delenv("LATEXTIFY_OFFLINE", raising=False)
    monkeypatch.delenv("TECTONIC_CACHE_DIR", raising=False)

    configure_offline_environment(tmp_path)

    assert os.environ["LATEXTIFY_OFFLINE"] == "1"
    assert os.environ["TECTONIC_CACHE_DIR"] == str(tmp_path / "tex-bundle-cache")
    assert os.environ["PATH"].split(os.pathsep)[0] == str(tmp_path / "tectonic")


def test_application_dir_uses_executable_when_frozen(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "LaTeXtify.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    assert application_dir() == tmp_path.resolve()


def test_choose_port_falls_back_when_preferred_is_occupied() -> None:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        occupied = int(listener.getsockname()[1])
        assert choose_port(occupied) != occupied


def test_offline_mode_never_downloads_missing_tectonic(monkeypatch) -> None:
    monkeypatch.setenv("LATEXTIFY_OFFLINE", "1")
    monkeypatch.setattr(tectonic, "find_tectonic", lambda: None)

    def unexpected_download():
        raise AssertionError("offline mode attempted a Tectonic download")

    monkeypatch.setattr(tectonic, "download_tectonic", unexpected_download)
    with pytest.raises(tectonic.TectonicNotAvailableError, match="offline package"):
        tectonic.ensure_tectonic()
