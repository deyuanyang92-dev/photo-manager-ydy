"""标本照片工作台 — 桌面版入口。

Usage:
    python main.py                 # normal launch (requires display)
    QT_QPA_PLATFORM=offscreen python main.py   # headless smoke check
"""
import sys
import os
import subprocess
import tempfile
from pathlib import Path

_runtime_dir = Path(tempfile.gettempdir()) / "specimen-photo-workbench"
_runtime_dir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_runtime_dir / "matplotlib"))
_HEADLESS_SMOKE = os.environ.get("QT_QPA_PLATFORM") == "offscreen"


def _qt_platform_works(platform: str) -> bool:
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = platform
    code = "from PyQt6.QtWidgets import QApplication; app = QApplication([])"
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except Exception:
        return False
    return proc.returncode == 0


_is_wsl = (
    sys.platform.startswith("linux")
    and "microsoft" in Path("/proc/version").read_text(errors="ignore").lower()
)
if _is_wsl and not os.environ.get("QT_QPA_PLATFORM"):
    candidates: list[str] = []
    if os.environ.get("DISPLAY"):
        candidates.append("xcb")
    if os.environ.get("WAYLAND_DISPLAY"):
        candidates.append("wayland")
    for candidate in candidates:
        if _qt_platform_works(candidate):
            os.environ["QT_QPA_PLATFORM"] = candidate
            break
    else:
        print(
            "无法启动 GUI：当前 WSL 环境的 Qt xcb/wayland 平台都不可用。\n"
            "将自动切到 offscreen，做无窗口启动冒烟测试。",
            file=sys.stderr,
        )
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        _HEADLESS_SMOKE = True

from PyQt6.QtWidgets import QApplication

from app.app_context import AppContext
from app.config.settings import AppSettings
from app.config.theme import apply_default_font, apply_theme, load_fonts, set_typography
from app.main_window import MainWindow
from app.views.registry import ALL_VIEWS


def main() -> int:
    # HiDPI: pass through the exact fractional scale (125%/150% on Windows,
    # Retina on macOS) instead of rounding it. Rounding mismatches QSS px
    # font-sizes against widget geometry → clipped/overlapping text on
    # fractional-DPI displays. Must be set before QApplication is constructed.
    from PyQt6.QtCore import Qt
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("标本照片工作台")
    app.setOrganizationName("SpecimenPhotoWorkbench")
    # ASCII app id for the WM/desktop layer. X11 WM_CLASS is Latin-1 only, so a
    # CJK applicationName leaks in as mojibake in GNOME's notification/title
    # ("「标本影像」 is ready"). An ASCII desktopFileName gives the WM a clean id
    # without touching applicationName (which keys QSettings storage).
    app.setDesktopFileName("specimen-photo-workbench")

    # ── App icon (window + taskbar). Multi-res .ico → crisp at every size;
    #    absent file degrades to Qt's default, never crashes. ──────────────
    from PyQt6.QtGui import QIcon
    _icon_path = Path(__file__).resolve().parent / "resources" / "branding" / "app.ico"
    if _icon_path.exists():
        app.setWindowIcon(QIcon(str(_icon_path)))

    # ── Fonts (bundled Noto Sans/Serif SC + JetBrains Mono if present;
    #    web-parity system fallback otherwise) ──────────────────────────
    load_fonts(app)
    # Apply the user's saved 字体 / 字体大小 (设置→界面) before pinning the default
    # font + building the theme QSS, so first paint already uses them.
    _s = AppSettings()
    set_typography(scale=_s.ui_font_scale, family=_s.ui_font_family)
    # Pin the default font to an installed CJK family BEFORE any widget is
    # built — otherwise first-paint layout uses Qt's CJK-less default ("Ubuntu"
    # on Linux), causing the startup text-overlap and garbled glyphs.
    apply_default_font(app)

    # ── Theme ─────────────────────────────────────────────────────────
    # Performance mode must be set before apply_theme (QSS drops gradients) and
    # before any card widget is built (apply_card_shadow becomes a no-op).
    from app.config import effects as _fx
    _fx.PERFORMANCE_MODE = _s.performance_mode
    app.setStyleSheet(apply_theme(_s.current_theme))

    # ── App context (shared state + DI container) ─────────────────────
    ctx = AppContext()

    # ── Collaboration service (P2P mDNS + FastAPI) ────────────────────
    # The service object always exists (so Settings can start it on demand),
    # but it only starts when the user has enabled collaboration AND set a
    # group code.  Empty code = no group = no sync.  Failures are swallowed —
    # collab is optional.
    try:
        from app.services.collab_service import CollabService
        svc = CollabService()
        ctx.collab_service = svc
        group_code = ctx.settings.team_code
        svc.set_group_code(group_code)
        if ctx.settings.collab_enabled and group_code:
            project_name = ctx.settings.last_project_dir or ""
            svc.start(project_name=project_name, group_code=group_code)
    except Exception:  # noqa: BLE001
        pass  # fastapi/uvicorn not installed or network unavailable

    # ── Main window ───────────────────────────────────────────────────
    win = MainWindow(ctx)

    # Register all 14 module views
    for view_cls in ALL_VIEWS:
        win.register_view(view_cls)

    # Restore last window state + nav selection
    win.restore_state()
    # WSLg often exposes a phantom/secondary RDP monitor; showMaximized can land
    # the window on a screen the user isn't looking at, making it look "missing".
    # Pin it to the primary screen before maximizing so it always shows up there.
    prim = app.primaryScreen()
    if prim is not None:
        win.setGeometry(prim.availableGeometry())
    win.showMaximized()   # open full-screen so the columns get room to breathe

    if _HEADLESS_SMOKE:
        app.processEvents()
        print("offscreen 启动冒烟通过：主窗口已构造完成。", file=sys.stderr)
        return 0

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
