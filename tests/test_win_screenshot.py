"""test_win_screenshot.py — WSL screenshot delegation helpers (Qt-free)."""
from __future__ import annotations

from app.utils import win_screenshot as ws


def test_is_wsl_reads_proc_version(monkeypatch, tmp_path):
    f = tmp_path / "version"
    f.write_text("Linux version 6.6.114.1-microsoft-standard-WSL2 ...")
    monkeypatch.setattr(ws, "Path", lambda *_: f)
    assert ws.is_wsl() is True


def test_is_wsl_false_on_native(monkeypatch, tmp_path):
    f = tmp_path / "version"
    f.write_text("Linux version 6.6.0-generic (gcc ...) ...")
    monkeypatch.setattr(ws, "Path", lambda *_: f)
    assert ws.is_wsl() is False


def test_snipper_prefers_snipaste(monkeypatch):
    monkeypatch.setattr(ws, "_cmd_exe", lambda: "/cmd.exe")
    monkeypatch.setattr(ws, "_has_snipaste", lambda: True)
    argv = ws.windows_snipper_cmd()
    assert argv is not None and "Snipaste" in argv and "snip" in argv


def test_snipper_falls_back_to_screenclip(monkeypatch):
    monkeypatch.setattr(ws, "_cmd_exe", lambda: "/cmd.exe")
    monkeypatch.setattr(ws, "_has_snipaste", lambda: False)
    argv = ws.windows_snipper_cmd()
    assert argv is not None and "ms-screenclip:" in argv


def test_snipper_none_without_cmd_exe(monkeypatch):
    monkeypatch.setattr(ws, "_cmd_exe", lambda: None)
    assert ws.windows_snipper_cmd() is None
