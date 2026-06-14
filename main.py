"""标本照片工作台 — 桌面版入口。

Usage:
    python main.py                 # normal launch (requires display)
    python main.py --check-gui     # diagnose WSLg/Qt display availability
    python main.py --smoke         # headless smoke check
    QT_QPA_PLATFORM=offscreen python main.py   # headless smoke check
"""
import sys
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

def _restore_last_project(ctx, win) -> bool:
    """启动时恢复上次打开的项目。

    只在 last_project_dir 仍是一个有效 workspace(目录存在 + 有 _data/project.db)
    时恢复;否则原样空项目(不强行打开失效/被删的路径,免得启动卡死或报错)。
    复刻手动打开项目的动作(main_window._open_project_dialog):设 current_project_dir
    + 刷新顶栏。返回是否成功恢复。
    """
    try:
        last = ctx.settings.last_project_dir
    except Exception:
        return False
    if not last or not os.path.isdir(last):
        return False
    if not os.path.isfile(os.path.join(last, "_data", "project.db")):
        return False  # 不是 workspace(没库)→ 不恢复
    try:
        ctx.current_project_dir = last
        if hasattr(win, "refresh_context_bar"):
            win.refresh_context_bar()
        return True
    except Exception:
        return False


def _writable_runtime_dir() -> Path:
    """A per-user runtime/cache dir that the *current* user can always write.

    Must be per-user: a fixed /tmp/<name> path is owned by whoever runs first,
    so a second user (e.g. running as root in tests, then as the real user)
    hits 'not a writable directory' and Matplotlib prints a startup warning.
    Suffixing with the uid avoids the collision; a final mkdtemp fallback
    covers the case where even that path is unusable.
    """
    uid = os.getuid() if hasattr(os, "getuid") else "user"
    candidate = Path(tempfile.gettempdir()) / f"specimen-photo-workbench-{uid}"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".w"
        probe.touch()
        probe.unlink()
        return candidate
    except OSError:
        return Path(tempfile.mkdtemp(prefix="specimen-photo-workbench-"))


_runtime_dir = _writable_runtime_dir()
_mpl_dir = _runtime_dir / "matplotlib"
_mpl_dir.mkdir(parents=True, exist_ok=True)
# Set unconditionally (not setdefault): a stale/unwritable inherited value would
# bring back the very warning we are killing.
os.environ["MPLCONFIGDIR"] = str(_mpl_dir)
_CHECK_GUI = "--check-gui" in sys.argv
if _CHECK_GUI:
    sys.argv.remove("--check-gui")
_HEADLESS_SMOKE = "--smoke" in sys.argv or os.environ.get("QT_QPA_PLATFORM") == "offscreen"
if "--smoke" in sys.argv:
    sys.argv.remove("--smoke")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

@dataclass(frozen=True)
class QtPlatformProbe:
    platform: str
    ok: bool
    returncode: int | None
    stderr: str


def _probe_qt_platform(platform: str, retries: int = 3) -> QtPlatformProbe:
    """Probe whether a Qt platform plugin can open a connection.

    Retries to absorb the WSLg boot race: DISPLAY is exported before the
    X server's socket (/tmp/.X11-unix/X0) is actually accepting clients, so
    a single probe right after boot can spuriously fail.
    """
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = platform
    code = "from PyQt6.QtWidgets import QApplication; app = QApplication([])"
    last_code: int | None = None
    last_stderr = ""
    for attempt in range(retries):
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=8,
                check=False,
            )
            last_code = proc.returncode
            last_stderr = proc.stderr.strip()
            if proc.returncode == 0:
                return QtPlatformProbe(platform, True, proc.returncode, last_stderr)
        except Exception as exc:  # noqa: BLE001
            last_stderr = str(exc)
        if attempt < retries - 1:
            time.sleep(0.5)
    return QtPlatformProbe(platform, False, last_code, last_stderr)


def _detect_wslg_display() -> None:
    """Backfill DISPLAY / WAYLAND_DISPLAY from on-disk WSLg sockets.

    Env vars are not always exported (sudo, non-login shells, cron), but the
    sockets are authoritative: if /tmp/.X11-unix/X0 exists, an X server is
    listening on :0 regardless of what the environment claims. Using the
    socket as ground truth is why launch stops being flaky.
    """
    if not os.environ.get("DISPLAY"):
        # WSLg always exposes display :0 via /tmp/.X11-unix/X0
        if Path("/tmp/.X11-unix/X0").exists() or Path("/mnt/wslg/.X11-unix/X0").exists():
            os.environ["DISPLAY"] = ":0"
    if not os.environ.get("WAYLAND_DISPLAY"):
        runtime = os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid())
        if (Path(runtime) / "wayland-0").exists() or Path("/mnt/wslg/runtime-dir/wayland-0").exists():
            os.environ["WAYLAND_DISPLAY"] = "wayland-0"
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if (
        Path("/mnt/wslg/runtime-dir/wayland-0").exists()
        and (not runtime or not (Path(runtime) / os.environ.get("WAYLAND_DISPLAY", "wayland-0")).exists())
    ):
        os.environ["XDG_RUNTIME_DIR"] = "/mnt/wslg/runtime-dir"


def _qt_candidates() -> list[str]:
    candidates: list[str] = []
    # xcb first: WSLg's X server is usually more reliable than its Wayland
    # socket ("Failed to create wl_display"). Prefer it whenever :0 is reachable.
    if os.environ.get("DISPLAY"):
        candidates.append("xcb")
    if os.environ.get("WAYLAND_DISPLAY"):
        candidates.append("wayland")
    return candidates


def _print_gui_diagnostics(probes: list[QtPlatformProbe] | None = None) -> None:
    print("GUI 环境诊断：", file=sys.stderr)
    print(f"  WSL: {'yes' if _is_wsl else 'no'}", file=sys.stderr)
    print(f"  uid: {os.geteuid() if hasattr(os, 'geteuid') else 'n/a'}", file=sys.stderr)
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "QT_QPA_PLATFORM"):
        print(f"  {key}: {os.environ.get(key) or '<empty>'}", file=sys.stderr)
    for path in ("/tmp/.X11-unix/X0", "/mnt/wslg/.X11-unix/X0", "/mnt/wslg/runtime-dir/wayland-0"):
        print(f"  {path}: {'exists' if Path(path).exists() else 'missing'}", file=sys.stderr)
    if probes:
        for probe in probes:
            status = "OK" if probe.ok else f"failed ({probe.returncode})"
            print(f"\n  Qt {probe.platform}: {status}", file=sys.stderr)
            if probe.stderr:
                for line in probe.stderr.splitlines()[:8]:
                    print(f"    {line}", file=sys.stderr)


def _print_gui_help(probes: list[QtPlatformProbe]) -> None:
    _print_gui_diagnostics(probes)
    root_hint = ""
    if _is_wsl and hasattr(os, "geteuid") and os.geteuid() == 0:
        root_hint = (
            "\n当前进程是 root。WSLg 经常拒绝 root/沙箱进程连接 Windows 桌面；"
            "请在普通 WSL 用户终端运行 `python3 main.py`，不要加 sudo。"
        )
    print(
        "\n无法启动 GUI：当前 WSL 环境的 Qt xcb/wayland 平台都不可用。\n"
        f"{root_hint}\n"
        "建议按顺序处理：\n"
        "  1. 在普通 WSL 用户终端运行，不要用 sudo/root 启动 GUI。\n"
        "  2. Windows PowerShell 执行 `wsl --update`，然后 `wsl --shutdown` 后重开 WSL。\n"
        "  3. Ubuntu/Debian WSL 安装 Qt X11 依赖：\n"
        "     `sudo apt update && sudo apt install -y libxcb-cursor0 libxcb-cursor-dev libxkbcommon-x11-0`\n"
        "  4. 诊断显示连接：`python3 main.py --check-gui`。\n"
        "  5. 只验证程序构造：`python3 main.py --smoke`。",
        file=sys.stderr,
    )


def _choose_startup_screen(screens, primary, cursor_screen):
    """Pick the screen where the main window should appear.

    In WSLg multi-monitor setups Qt's screen order is unstable, and the old
    "nearest to (0,0)" rule opens the app on a monitor the user is not looking
    at.  Qt primary is the least surprising default because it matches where
    the window manager/taskbar expects new windows; cursor is only a fallback
    because remote launches can report a stale/default cursor position.
    """
    screens = [s for s in (screens or []) if s is not None]
    if primary in screens:
        return primary
    if cursor_screen in screens:
        return cursor_screen
    return screens[0] if screens else None


def _startup_target_screen(app):
    """Resolve the startup screen after QApplication exists."""
    cursor_screen = None
    try:
        from PyQt6.QtGui import QCursor
        cursor_screen = app.screenAt(QCursor.pos())
    except Exception:  # noqa: BLE001
        cursor_screen = None
    return _choose_startup_screen(app.screens(), app.primaryScreen(), cursor_screen)


def _screen_label(screen) -> str:
    if screen is None:
        return "<none>"
    try:
        g = screen.geometry()
        return f"{screen.name()} {g.x()},{g.y()} {g.width()}x{g.height()}"
    except Exception:  # noqa: BLE001
        return str(screen)


def _window_on_any_screen(win, screens) -> bool:
    """Return True when the restored window frame overlaps a visible screen."""
    try:
        frame = win.frameGeometry()
        if frame.isNull() or frame.width() <= 1 or frame.height() <= 1:
            return False
        for screen in screens or []:
            if screen is not None and screen.availableGeometry().intersects(frame):
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def _place_main_window(win, target) -> None:
    """Put the main window on *target* and make it foreground-visible."""
    from PyQt6.QtCore import Qt

    if target is not None:
        avail = target.availableGeometry()
        win.showNormal()
        win.setGeometry(avail)
    win.setWindowState(
        (win.windowState() & ~Qt.WindowState.WindowMinimized)
        | Qt.WindowState.WindowMaximized
        | Qt.WindowState.WindowActive
    )
    win.showMaximized()
    win.raise_()
    win.activateWindow()


def _show_main_window_at_startup(win, app, target) -> str:
    """Show the main window without destroying a valid restored position."""
    from PyQt6.QtCore import Qt

    if _window_on_any_screen(win, app.screens()):
        win.setWindowState(
            (win.windowState() & ~Qt.WindowState.WindowMinimized)
            | Qt.WindowState.WindowMaximized
            | Qt.WindowState.WindowActive
        )
        win.showMaximized()
        win.raise_()
        win.activateWindow()
        return "restored"
    _place_main_window(win, target)
    return "fallback"


def _ensure_main_window_visible(win, app, target) -> None:
    """Delayed startup rescue for WSLg/window-manager focus races."""
    try:
        if not _window_on_any_screen(win, app.screens()):
            _place_main_window(win, target)
        else:
            win.raise_()
            win.activateWindow()
        app.alert(win, 3000)
    except Exception:  # noqa: BLE001
        pass


def _install_exception_hook(win) -> None:
    """Route uncaught Qt-slot errors to both stderr and a copyable dialog."""
    import traceback

    old_hook = sys.excepthook

    def _hook(exc_type, exc, tb):
        old_hook(exc_type, exc, tb)
        if _HEADLESS_SMOKE or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        try:
            from app.utils import ui
            detail = "".join(traceback.format_exception(exc_type, exc, tb))
            ui.critical(
                win,
                "程序遇到错误",
                str(exc) or exc_type.__name__,
                informative_text="操作没有按预期完成。展开详细信息可复制给维护者排查。",
                detailed_text=detail,
            )
        except Exception:  # noqa: BLE001
            pass

    sys.excepthook = _hook


_is_wsl = (
    sys.platform.startswith("linux")
    and "microsoft" in Path("/proc/version").read_text(errors="ignore").lower()
)
if _is_wsl and not _CHECK_GUI and not os.environ.get("QT_QPA_PLATFORM"):
    _detect_wslg_display()
    probes: list[QtPlatformProbe] = []
    for candidate in _qt_candidates():
        probe = _probe_qt_platform(candidate)
        probes.append(probe)
        if probe.ok:
            os.environ["QT_QPA_PLATFORM"] = candidate
            break
    else:
        _print_gui_help(probes)
        sys.exit(2)

if _CHECK_GUI:
    if _is_wsl and not os.environ.get("QT_QPA_PLATFORM"):
        _detect_wslg_display()
    platforms = [os.environ["QT_QPA_PLATFORM"]] if os.environ.get("QT_QPA_PLATFORM") else _qt_candidates()
    probes = [_probe_qt_platform(platform, retries=1) for platform in platforms]
    if any(probe.ok for probe in probes):
        _print_gui_diagnostics(probes)
        sys.exit(0)
    _print_gui_help(probes)
    sys.exit(2)

from PyQt6.QtCore import QTimer
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

    # ── Language ──────────────────────────────────────────────────────
    # Apply the saved language BEFORE any widget/view is built so first paint is
    # in the right language. Switching at runtime is live (Settings →
    # MainWindow.retranslate_ui), so no restart is needed thereafter.
    from app.config.i18n import set_language
    set_language(_s.current_language)

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
    _install_exception_hook(win)

    # Central shutdown hook: closeEvent already calls win._teardown(), but if
    # the app exits any other way (crash, lastWindowClosed, OS signal, the
    # offscreen smoke path) the DB connections would leak. On WSL/drvfs that
    # leak locks the per-project SQLite DB until a reboot — the root cause of
    # "close → reopen → must reboot". aboutToQuit is the guaranteed last stop.
    app.aboutToQuit.connect(win._teardown)

    # OS-signal → Qt quit bridge. On WSLg, closing the Windows-side window or
    # killing the wsl.exe parent does NOT always deliver a Qt closeEvent — the
    # XWayland socket can drop, leaving python alive with the window gone and
    # aboutToQuit never firing. Translating SIGTERM/SIGINT/SIGHUP into
    # app.quit() makes _teardown (→ close_all DB) reachable on that exact
    # "window closed but process lingers" path. Qt swallows SIGINT for its own
    # event loop, so install before exec.
    import signal as _signal
    for _sig in (_signal.SIGTERM, _signal.SIGINT,
                 getattr(_signal, "SIGHUP", None)):
        if _sig is None:
            continue
        try:
            _signal.signal(_sig, lambda *_a: app.quit())
        except (ValueError, OSError):  # not main thread / unsupported
            pass

    # Register all 14 module views
    for view_cls in ALL_VIEWS:
        win.register_view(view_cls)

    # 启动自动恢复上次项目——免得每次重启都回到 "(未选)" 空项目,用户得重选。
    _restore_last_project(ctx, win)

    # Restore last nav selection and saved docking state.
    win.restore_state()
    # WSLg multi-monitor ordering is unstable across boots and Windows display
    # changes.  The old nearest-to-(0,0) rule often opened the app on a monitor
    # the user was not looking at.  Prefer Qt primary, then cursor screen, and
    # force a second delayed raise to absorb WM races.
    target = _startup_target_screen(app)
    if not _HEADLESS_SMOKE:
        print(f"启动窗口目标屏幕: {_screen_label(target)}", file=sys.stderr)
    placement = _show_main_window_at_startup(win, app, target)
    if not _HEADLESS_SMOKE:
        print(f"启动窗口放置策略: {placement}", file=sys.stderr)
        # offscreen plugin warns "does not support raise()"; only needed for a
        # real window manager anyway (pull the window to the front + focus it).
        QTimer.singleShot(250, lambda: _ensure_main_window_visible(win, app, target))
        QTimer.singleShot(1000, lambda: _ensure_main_window_visible(win, app, target))

    if _HEADLESS_SMOKE:
        app.processEvents()
        print("offscreen 启动冒烟通过：主窗口已构造完成。", file=sys.stderr)
        return 0

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
