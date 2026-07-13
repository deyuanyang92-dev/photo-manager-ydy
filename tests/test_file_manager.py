from __future__ import annotations


def test_open_directory_uses_windows_explorer_from_wsl(monkeypatch):
    from app.utils import file_manager
    from app.utils import path_utils

    calls = []
    monkeypatch.setattr(path_utils.sys, "platform", "linux")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setattr(
        file_manager.subprocess,
        "Popen",
        lambda argv: calls.append(argv),
    )

    assert file_manager.open_directory("/mnt/n/claude/zhengli") is True
    assert calls == [["explorer.exe", "N:\\claude\\zhengli"]]


def test_open_directory_localizes_wsl_path_on_windows(monkeypatch):
    from app.utils import file_manager
    from app.utils import path_utils

    calls = []
    monkeypatch.setattr(path_utils.sys, "platform", "win32")
    monkeypatch.setattr(file_manager.sys, "platform", "win32")
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.setattr(file_manager.os, "startfile", lambda path: calls.append(path))

    assert file_manager.open_directory("/mnt/n/claude/zhengli") is True
    assert calls == ["N:\\claude\\zhengli"]


def test_open_directory_windows_falls_back_to_explorer_and_reports_local_path(monkeypatch):
    from app.utils import file_manager
    from app.utils import path_utils

    calls = []
    monkeypatch.setattr(path_utils.sys, "platform", "win32")
    monkeypatch.setattr(file_manager.sys, "platform", "win32")
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.setattr(
        file_manager.os,
        "startfile",
        lambda _path: (_ for _ in ()).throw(OSError("startfile failed")),
    )
    monkeypatch.setattr(file_manager.subprocess, "Popen", lambda argv: calls.append(argv))

    result = file_manager.open_directory_detailed("/mnt/n/claude/zhengli")

    assert result.opened is True
    assert result.local_path == "N:\\claude\\zhengli"
    assert calls == [["explorer.exe", "N:\\claude\\zhengli"]]


def test_open_directory_reports_both_windows_launcher_errors(monkeypatch):
    from app.utils import file_manager
    from app.utils import path_utils

    monkeypatch.setattr(path_utils.sys, "platform", "win32")
    monkeypatch.setattr(file_manager.sys, "platform", "win32")
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.setattr(
        file_manager.os,
        "startfile",
        lambda _path: (_ for _ in ()).throw(OSError("startfile failed")),
    )
    monkeypatch.setattr(
        file_manager.subprocess,
        "Popen",
        lambda _argv: (_ for _ in ()).throw(OSError("explorer failed")),
    )

    result = file_manager.open_directory_detailed("C:/missing/project")

    assert result.opened is False
    assert result.local_path == "C:\\missing\\project"
    assert "startfile failed" in result.error
    assert "explorer failed" in result.error


def test_reveal_file_selects_windows_path_from_wsl(monkeypatch):
    from app.utils import file_manager
    from app.utils import path_utils

    calls = []
    monkeypatch.setattr(path_utils.sys, "platform", "linux")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setattr(file_manager.Path, "is_dir", lambda _self: False)
    monkeypatch.setattr(
        file_manager.subprocess,
        "Popen",
        lambda argv: calls.append(argv),
    )

    assert file_manager.reveal_in_directory("/mnt/n/claude/zhengli/a.tif") is True
    assert calls == [["explorer.exe", "/select,", "N:\\claude\\zhengli\\a.tif"]]
