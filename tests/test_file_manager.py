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
    monkeypatch.setattr(
        file_manager.subprocess,
        "Popen",
        lambda argv: calls.append(argv),
    )

    assert file_manager.open_directory("/mnt/n/claude/zhengli") is True
    assert calls == [["explorer", "N:\\claude\\zhengli"]]


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
