def test_file_manager_profile_detects_windows(monkeypatch):
    from app.config import theme

    monkeypatch.delenv("SPECIMEN_FILE_MANAGER_STYLE", raising=False)
    monkeypatch.setattr(theme.sys, "platform", "win32")

    assert theme._detect_file_manager_profile() == "windows"


def test_file_manager_profile_detects_wsl_as_windows(monkeypatch):
    from app.config import theme

    monkeypatch.delenv("SPECIMEN_FILE_MANAGER_STYLE", raising=False)
    monkeypatch.setattr(theme.sys, "platform", "linux")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")

    assert theme._detect_file_manager_profile() == "windows"


def test_file_manager_profile_detects_macos(monkeypatch):
    from app.config import theme

    monkeypatch.delenv("SPECIMEN_FILE_MANAGER_STYLE", raising=False)
    monkeypatch.setattr(theme.sys, "platform", "darwin")

    assert theme._detect_file_manager_profile() == "macos"


def test_file_manager_qss_uses_system_file_row_rules(monkeypatch):
    from app.config import theme

    monkeypatch.setenv("SPECIMEN_FILE_MANAGER_STYLE", "windows")
    qss = theme.apply_theme("classic_light")

    assert "System-like file rows" in qss
    assert "QFrame#CardSelected" in qss
    assert "QFrame#Card[resultSelected=\"true\"]" in qss
    assert "#d7ebff" in qss
    assert "border-left: 5px solid #1d6fd1" in qss
    assert "QLabel#FileSelectMark[selected=\"true\"]" in qss
    assert "QLabel#ResultSelectBadge[selected=\"true\"]" in qss
