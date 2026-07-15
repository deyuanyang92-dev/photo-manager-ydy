"""标本照片工作台 — 桌面版入口。

Usage:
    python main.py                 # normal launch (requires display)
    python main.py --check-gui     # diagnose WSLg/Qt display availability
    python main.py --smoke         # headless smoke check
    QT_QPA_PLATFORM=offscreen python main.py   # headless smoke check
"""
import sys
import os
import logging
import subprocess
import tempfile
import time
import importlib.util
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
        from app.utils.path_utils import localize_path
        last = localize_path(ctx.settings.last_project_dir)
    except Exception:
        return False
    if not last or not os.path.isdir(last):
        return False
    if not os.path.isfile(os.path.join(last, "_data", "project.db")):
        return False  # 不是 workspace(没库)→ 不恢复
    try:
        saved_root = localize_path(ctx.settings.project_tree_root)
        root = last
        if saved_root and os.path.isdir(saved_root):
            last_abs = os.path.abspath(last)
            root_abs = os.path.abspath(saved_root)
            try:
                if os.path.commonpath((last_abs, root_abs)) == root_abs:
                    root = saved_root
            except ValueError:
                pass  # Different drives: the saved root cannot own this workspace.
        from app.services.project_service import enter_workspace
        enter_workspace(ctx, last, root=root)
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
_INSTANCE_LOCK_HANDLE = None
_INSTANCE_MUTEX_HANDLE = None
_QT_MESSAGE_HANDLER = None
_QT_PREVIOUS_MESSAGE_HANDLER = None
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


def _safe_stderr_print(*args, **kwargs) -> None:
    """Best-effort stderr logging; a closed launcher pipe must not crash startup."""
    kwargs.pop("file", None)
    try:
        print(*args, file=sys.stderr, **kwargs)
    except (BrokenPipeError, OSError):
        pass


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
    _safe_stderr_print("GUI 环境诊断：")
    _safe_stderr_print(f"  WSL: {'yes' if _is_wsl else 'no'}")
    _safe_stderr_print(f"  uid: {os.geteuid() if hasattr(os, 'geteuid') else 'n/a'}")
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "QT_QPA_PLATFORM"):
        _safe_stderr_print(f"  {key}: {os.environ.get(key) or '<empty>'}")
    for path in ("/tmp/.X11-unix/X0", "/mnt/wslg/.X11-unix/X0", "/mnt/wslg/runtime-dir/wayland-0"):
        _safe_stderr_print(f"  {path}: {'exists' if Path(path).exists() else 'missing'}")
    if probes:
        for probe in probes:
            status = "OK" if probe.ok else f"failed ({probe.returncode})"
            _safe_stderr_print(f"\n  Qt {probe.platform}: {status}")
            if probe.stderr:
                for line in probe.stderr.splitlines()[:8]:
                    _safe_stderr_print(f"    {line}")


def _print_gui_help(probes: list[QtPlatformProbe]) -> None:
    _print_gui_diagnostics(probes)
    root_hint = ""
    if _is_wsl and hasattr(os, "geteuid") and os.geteuid() == 0:
        root_hint = (
            "\n当前进程是 root。WSLg 经常拒绝 root/沙箱进程连接 Windows 桌面；"
            "请在普通 WSL 用户终端运行 `python3 main.py`，不要加 sudo。"
        )
    extra_hint = ""
    if any("libEGL.so.1" in probe.stderr for probe in probes):
        extra_hint = (
            "\n当前报错是 `libEGL.so.1` 缺失，优先补系统 EGL / OpenGL 运行库：\n"
            "  sudo apt update && sudo apt install -y libegl1 libgl1\n"
        )
    _safe_stderr_print(
        "\n无法启动 GUI：当前 WSL 环境的 Qt xcb/wayland 平台都不可用。\n"
        f"{root_hint}\n"
        f"{extra_hint}"
        "建议按顺序处理：\n"
        "  1. 在普通 WSL 用户终端运行，不要用 sudo/root 启动 GUI。\n"
        "  2. Windows PowerShell 执行 `wsl --update`，然后 `wsl --shutdown` 后重开 WSL。\n"
        "  3. Ubuntu/Debian WSL 安装 Qt X11 依赖：\n"
        "     `sudo apt update && sudo apt install -y libxcb-cursor0 libxcb-cursor-dev libxkbcommon-x11-0`\n"
        "  4. 诊断显示连接：`python3 main.py --check-gui`。\n"
        "  5. 只验证程序构造：`python3 main.py --smoke`。",
    )


def _print_missing_pyqt6_help() -> None:
    _safe_stderr_print(
        "GUI 启动失败：当前 Python 环境未安装 PyQt6。\n"
        "\n"
        "请先在当前环境执行：\n"
        "  python3 -m pip install -r requirements.txt\n"
        "\n"
        "如果你在 conda/venv 里运行，请先激活同一个环境再安装。\n"
        "在 WSL 里额外还需要系统 Qt X11 依赖，但现在这一步还没走到。",
    )


def _print_missing_qt_runtime_help(detail: str) -> None:
    _safe_stderr_print(
        "GUI 启动失败：PyQt6 已安装，但 Qt 运行时依赖缺失。\n"
        f"  详细错误: {detail}\n"
        "\n"
        "这通常表示系统里的 EGL / OpenGL / X11 运行库没装全。\n"
        "在 Ubuntu/Debian WSL 里优先尝试：\n"
        "  sudo apt update\n"
        "  sudo apt install -y libegl1 libgl1 libxcb-cursor0 libxkbcommon-x11-0\n"
        "\n"
        "如果 `sudo` 需要密码，这一步需要你在本机终端执行。",
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


def _should_default_performance_mode(
    *, is_wsl: bool, platform: str, low_memory: bool, setting_present: bool
) -> bool:
    """Choose the safe first-run rendering default without overriding users."""
    if setting_present:
        return False
    return bool(is_wsl or platform == "win32" or low_memory)


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


def _adaptive_window_geometry(available, minimum):
    """Return the centered first-start rect for one screen's available area."""
    from PyQt6.QtCore import QRect
    from app.config.window_layout import FIRST_START_WORK_AREA_FRACTION

    width = min(
        available.width(),
        max(minimum.width(), round(available.width() * FIRST_START_WORK_AREA_FRACTION)),
    )
    height = min(
        available.height(),
        max(minimum.height(), round(available.height() * FIRST_START_WORK_AREA_FRACTION)),
    )
    left = available.x() + (available.width() - width) // 2
    top = available.y() + (available.height() - height) // 2
    return QRect(left, top, width, height)


def _place_main_window(win, target) -> None:
    """Show a first-run/off-screen window once, centered at 80% work area."""
    from PyQt6.QtCore import QSize, Qt

    if target is not None:
        avail = target.availableGeometry()
        minimum = win.minimumSize() if hasattr(win, "minimumSize") else QSize(1, 1)
        win.setGeometry(_adaptive_window_geometry(avail, minimum))
    win.setWindowState(
        win.windowState()
        & ~Qt.WindowState.WindowMinimized
        & ~Qt.WindowState.WindowMaximized
        | Qt.WindowState.WindowActive
    )
    win.showNormal()
    win.raise_()
    win.activateWindow()


def _show_main_window_at_startup(
    win, app, target, *, has_saved_geometry: bool = False
) -> str:
    """Show restored user geometry, or use the adaptive first-start policy."""
    from PyQt6.QtCore import Qt

    if has_saved_geometry and _window_on_any_screen(win, app.screens()):
        restored_state = win.windowState()
        was_maximized = bool(restored_state & Qt.WindowState.WindowMaximized)
        win.setWindowState(
            (restored_state & ~Qt.WindowState.WindowMinimized)
            | Qt.WindowState.WindowActive
        )
        if was_maximized:
            win.showMaximized()
            placement = "restored-maximized"
        else:
            win.showNormal()
            placement = "restored-normal"
        win.raise_()
        win.activateWindow()
        return placement
    _place_main_window(win, target)
    return "adaptive-first-start"


def _stabilize_main_window_after_show(
    win, target, *, use_adaptive_geometry: bool
) -> bool:
    """Undo a native startup minimize after the first Windows event-loop turn."""
    from PyQt6.QtCore import QSize, Qt

    minimized = bool(win.isMinimized())
    # Claude Code 修改 2026-07-14 — 稳定器只该撤销原生启动最小化;窗口未最小化时无论有无存档几何都提前返回,避免首启后回抢用户焦点
    # if not minimized and not use_adaptive_geometry:
    if not minimized:
        return False

    state = win.windowState() & ~Qt.WindowState.WindowMinimized
    if use_adaptive_geometry:
        state &= ~Qt.WindowState.WindowMaximized
        if target is not None:
            minimum = win.minimumSize() if hasattr(win, "minimumSize") else QSize(1, 1)
            win.setGeometry(
                _adaptive_window_geometry(target.availableGeometry(), minimum)
            )
    win.setWindowState(state | Qt.WindowState.WindowActive)
    if state & Qt.WindowState.WindowMaximized:
        win.showMaximized()
    else:
        win.showNormal()
    win.raise_()
    win.activateWindow()
    return True


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


def _is_main_qt_thread() -> bool:
    """True only when the caller runs on the GUI (QApplication) thread.

    Constructing/exec()ing a QWidget outside the GUI thread is undefined
    behaviour in Qt: the box is parented across threads
    ("QObject::setParent: Cannot set parent, new parent is in a different
    thread"), its exec() drives the *whole* widget stack from the worker
    thread ("QBasicTimer::start: Timers cannot be started from another
    thread" ×N, "QWidget::repaint: Recursive repaint detected") and the real
    GUI loop starves → the window goes "未响应".
    """
    try:
        from PyQt6.QtCore import QCoreApplication, QThread
    except ImportError:  # PyQt6 absent (library import path) — nothing to guard
        return True
    qapp = QCoreApplication.instance()
    if qapp is None:
        return False
    return QThread.currentThread() is qapp.thread()


def _install_exception_hook(win):
    """Route uncaught Qt-slot errors to both stderr and a copyable dialog.

    The dialog is ALWAYS built on the GUI thread: uncaught exceptions escaping a
    ``QThread.run()`` (e.g. collab_net's uvicorn config) reach ``sys.excepthook``
    *on that worker thread*, so the hook may not touch any QWidget directly.
    Worker threads only ``emit`` — the signal is delivered to ``_ErrorReporter``
    (main-thread affinity) as a QueuedConnection, and the QMessageBox is created
    there.
    """
    import traceback

    old_hook = sys.excepthook
    reporter = _ErrorReporter(win) if _ErrorReporter is not None else None
    globals()["_ERROR_REPORTER"] = reporter  # keep a strong ref (also parented to win)

    def _hook(exc_type, exc, tb):
        old_hook(exc_type, exc, tb)
        detail = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            diagnostics.setup_logging()
            logging.getLogger("app.uncaught").error(
                "Uncaught exception",
                exc_info=(exc_type, exc, tb),
            )
        except Exception:  # noqa: BLE001
            pass
        if _HEADLESS_SMOKE or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        text = str(exc) or exc_type.__name__
        # §7 旧实现（无线程闸门，任何线程都直接构造 QMessageBox）保留备查：
        # try:
        #     from app.utils import ui
        #     ui.critical(
        #         win,
        #         "程序遇到错误",
        #         str(exc) or exc_type.__name__,
        #         informative_text=(
        #             "操作没有按预期完成，错误已写入日志。\n"
        #             f"日志文件：{diagnostics.log_path()}\n"
        #             "展开详细信息或点击“复制详情”可复制给维护者排查。"
        #         ),
        #         detailed_text=detail,
        #     )
        # except Exception:  # noqa: BLE001
        #     pass
        if not _is_main_qt_thread():
            # 非 GUI 线程：绝不构造/exec 任何 QWidget，只把内容甩回主线程。
            if reporter is not None:
                try:
                    reporter.error.emit(text, detail)
                except Exception:  # noqa: BLE001
                    pass
            return
        _show_error_dialog(win, text, detail)

    sys.excepthook = _hook
    return reporter


def _show_error_dialog(win, text: str, detail: str) -> None:
    """Build + exec the error QMessageBox. GUI-thread only (callers must check)."""
    try:
        from app.utils import ui
        ui.critical(
            win,
            "程序遇到错误",
            text,
            informative_text=(
                "操作没有按预期完成，错误已写入日志。\n"
                f"日志文件：{diagnostics.log_path()}\n"
                "展开详细信息或点击“复制详情”可复制给维护者排查。"
            ),
            detailed_text=detail,
        )
    except Exception:  # noqa: BLE001
        pass


# ── Qt 内部噪声过滤 ───────────────────────────────────────────────────────
# Qt 6.10 的 windows11 样式 + 我们 QSS 里的像素字号 (`QWidget { font-size: 13px }`,
# theme.py 的 font_* token 全是 px) 组合后，Qt 自己会在内部把 QFont.pointSize()
# (px 定义的字体 → 恒为 -1) 回填给 QFont::setPointSize()，于是每建一个
# QComboBox / QCalendarWidget / 带日历弹窗的 QDateEdit 就刷几条
#     QFont::setPointSize: Point size <= 0 (-1), must be greater than 0
# 一次启动能刷出几十上百条。已实测确认：
#   * 纯 Qt 最小复现(不含本项目任何代码): setStyleSheet("QWidget{font-size:13px}")
#     + QComboBox/QCalendarWidget → windows11 样式 14 条；fusion / windowsvista
#     / 不加 QSS → 0 条；Linux(Qt 6.11, Fusion) → 0 条。
#   * 复现时 Python 调用栈里没有任何本项目帧 —— 调用发生在 Qt C++ 内部，
#     我们没有可修的调用点(项目自己的 setPointSize 调用全是正数常量或已 clamp)。
# 既然改不了调用方，又不能为了它改字号(px→pt 会改变实际观感，违反 UI 冻结)，
# 就在日志桥这里把这条已知噪声丢掉。设 SPECIMEN_QT_VERBOSE=1 可以放行，
# 便于日后排查我们自己代码真的传了 <=0 的情况。
_QT_NOISE_SUBSTRINGS = (
    "QFont::setPointSize: Point size <= 0",
    "QFont::setPointSizeF: Point size <= 0",
)


def _is_qt_noise(message: str) -> bool:
    """已知的、无害的 Qt 内部告警 —— 不写日志，也不转发给上一个 handler。"""
    if os.environ.get("SPECIMEN_QT_VERBOSE"):
        return False
    text = message or ""
    return any(noise in text for noise in _QT_NOISE_SUBSTRINGS)


def _install_qt_message_handler(installer=None) -> None:
    """Bridge Qt runtime warnings into the rotating application log."""
    global _QT_MESSAGE_HANDLER, _QT_PREVIOUS_MESSAGE_HANDLER
    if _QT_MESSAGE_HANDLER is not None:
        return

    from PyQt6.QtCore import QtMsgType, qInstallMessageHandler

    install = installer or qInstallMessageHandler
    previous_holder = {"handler": None}

    def _handler(message_type, context, message) -> None:
        # §7 旧逻辑(无过滤，所有 Qt 消息一律入日志)保留在下面注释里：
        # levels = { ... }   ← 原来直接从这里开始，没有噪声过滤
        if _is_qt_noise(message):
            return
        levels = {
            QtMsgType.QtDebugMsg: logging.DEBUG,
            QtMsgType.QtInfoMsg: logging.INFO,
            QtMsgType.QtWarningMsg: logging.WARNING,
            QtMsgType.QtCriticalMsg: logging.ERROR,
            QtMsgType.QtFatalMsg: logging.CRITICAL,
        }
        source = ""
        if context is not None:
            file_name = getattr(context, "file", None) or ""
            line = getattr(context, "line", 0) or 0
            function = getattr(context, "function", None) or ""
            if file_name or function:
                source = f" [{file_name}:{line} {function}]"
        logging.getLogger("qt").log(
            levels.get(message_type, logging.WARNING),
            "%s%s",
            message,
            source,
        )
        previous = previous_holder["handler"]
        if callable(previous):
            previous(message_type, context, message)

    previous_holder["handler"] = install(_handler)
    _QT_PREVIOUS_MESSAGE_HANDLER = previous_holder["handler"]
    _QT_MESSAGE_HANDLER = _handler


def _detect_is_wsl() -> bool:
    """Safe WSL check — never raise on missing/unreadable /proc/version."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        text = Path("/proc/version").read_text(errors="ignore").lower()
    except OSError:
        return False
    return "microsoft" in text


_is_wsl = _detect_is_wsl()


def _bootstrap_cli_runtime() -> None:
    """WSL Qt probe / --check-gui. Call only from CLI entry — never on import.

    Importing ``main`` (tests, ``from main import …``) must not ``sys.exit``.
    """
    if importlib.util.find_spec("PyQt6") is None:
        _print_missing_pyqt6_help()
        sys.exit(2)

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
        platforms = (
            [os.environ["QT_QPA_PLATFORM"]]
            if os.environ.get("QT_QPA_PLATFORM")
            else _qt_candidates()
        )
        probes = [_probe_qt_platform(platform, retries=1) for platform in platforms]
        if any(probe.ok for probe in probes):
            _print_gui_diagnostics(probes)
            sys.exit(0)
        _print_gui_help(probes)
        sys.exit(2)


# Qt / app imports: allow ``from main import _choose_startup_screen`` without
# forcing a process exit when PyQt6 is absent (CLI path re-checks below).
try:
    from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot
    from PyQt6.QtWidgets import QApplication

    from app.app_context import AppContext
    from app.config.settings import AppSettings
    from app.config.theme import apply_default_font, apply_theme, load_fonts, set_typography
    from app.main_window import MainWindow
    from app.utils import diagnostics
    from app.views.registry import ALL_VIEW_SPECS

    _QT_IMPORT_ERROR: BaseException | None = None
except ImportError as _qt_exc:  # noqa: BLE001 — soft-fail for library import
    QObject = None  # type: ignore[misc, assignment]
    QTimer = None  # type: ignore[misc, assignment]
    pyqtSignal = None  # type: ignore[misc, assignment]
    pyqtSlot = None  # type: ignore[misc, assignment]
    QApplication = None  # type: ignore[misc, assignment]
    AppContext = None  # type: ignore[misc, assignment]
    AppSettings = None  # type: ignore[misc, assignment]
    apply_default_font = None  # type: ignore[misc, assignment]
    apply_theme = None  # type: ignore[misc, assignment]
    load_fonts = None  # type: ignore[misc, assignment]
    set_typography = None  # type: ignore[misc, assignment]
    MainWindow = None  # type: ignore[misc, assignment]
    diagnostics = None  # type: ignore[misc, assignment]
    ALL_VIEW_SPECS = ()  # type: ignore[misc, assignment]
    _QT_IMPORT_ERROR = _qt_exc


# ── 跨线程错误上报 ────────────────────────────────────────────────────────
# 唯一 100% 安全的跨线程弹窗姿势：主线程 QObject + pyqtSignal。signal 可以从任意
# 线程 emit，AutoConnection 会自动排队(QueuedConnection)到接收者所属线程执行槽。
# 不用 QTimer.singleShot 兜底：Qt6 的 QSingleShotTimer 在非 GUI 线程构造时仍可能
# 在错误线程上 startTimer，复现同类 QBasicTimer 警告(❓未实测，不值得赌)。
_ERROR_REPORTER = None

if QObject is not None:

    class _ErrorReporter(QObject):
        """Marshals worker-thread error reports onto the GUI thread."""

        error = pyqtSignal(str, str)  # text, detail

        def __init__(self, win):
            super().__init__(win)  # 父=MainWindow → affinity 主线程
            self._win = win
            self.error.connect(self._show)  # 跨线程 emit ⇒ QueuedConnection

        @pyqtSlot(str, str)
        def _show(self, text: str, detail: str) -> None:
            _show_error_dialog(self._win, text, detail)

else:  # PyQt6 absent — importing main as a library must still work
    _ErrorReporter = None  # type: ignore[assignment]


def _acquire_single_instance_lock() -> bool:
    """Return False when another GUI instance is already running.

    Multiple app windows against the same project can leave SQLite waiting on
    WAL locks, especially under WSL /mnt drives.  Tests and smoke checks opt out
    so they can construct windows freely.
    """
    global _INSTANCE_LOCK_HANDLE
    if _HEADLESS_SMOKE or os.environ.get("SPECIMEN_WORKBENCH_ALLOW_MULTI") == "1":
        return True
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            name = "Local\\SpecimenPhotoWorkbench.SingleInstance"
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = (
                wintypes.LPVOID,
                wintypes.BOOL,
                wintypes.LPCWSTR,
            )
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            handle = kernel32.CreateMutexW(None, True, name)
            if not handle:
                return True
            if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
                kernel32.CloseHandle(handle)
                return False
            globals()["_INSTANCE_MUTEX_HANDLE"] = handle
            return True
        except Exception:
            return True
    try:
        import fcntl
    except ImportError:
        return True
    lock_path = _runtime_dir / "app.lock"
    try:
        handle = lock_path.open("a+", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        _INSTANCE_LOCK_HANDLE = handle
        return True
    except BlockingIOError:
        try:
            handle.close()
        except Exception:
            pass
        return False
    except OSError:
        return True


def main() -> int:
    startup_t0 = time.perf_counter()

    def startup_mark(stage: str) -> None:
        logging.getLogger(__name__).info(
            "Startup stage %-24s %7.1f ms",
            stage,
            (time.perf_counter() - startup_t0) * 1000,
        )

    if _QT_IMPORT_ERROR is not None:
        _print_missing_qt_runtime_help(str(_QT_IMPORT_ERROR))
        return 2
    if QApplication is None or diagnostics is None:
        _print_missing_pyqt6_help()
        return 2

    log_path = diagnostics.setup_logging()
    crash_log_path = diagnostics.install_runtime_diagnostics()
    _install_qt_message_handler()
    logging.getLogger(__name__).info(
        "Application starting argv=%s cwd=%s log=%s crash_log=%s",
        sys.argv,
        os.getcwd(),
        log_path,
        crash_log_path,
    )
    startup_mark("logging-ready")

    # HiDPI: pass through the exact fractional scale (125%/150% on Windows,
    # Retina on macOS) instead of rounding it. Rounding mismatches QSS px
    # font-sizes against widget geometry → clipped/overlapping text on
    # fractional-DPI displays. Must be set before QApplication is constructed.
    from PyQt6.QtCore import Qt
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    startup_mark("qapplication-ready")
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

    if not _acquire_single_instance_lock():
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(
            None,
            "程序已经在运行",
            "标本影像已经打开了一个窗口。\n\n"
            "请先使用已有窗口；如果旧窗口已经看不见，请在 WSL 终端结束旧的 "
            "`python3 main.py` 进程后再启动。",
        )
        return 0

    # ── Fonts (bundled Noto Sans/Serif SC + JetBrains Mono if present;
    #    web-parity system fallback otherwise) ──────────────────────────
    load_fonts(app)
    # Apply the user's saved 字体 / 字体大小 (设置→界面) before pinning the default
    # font + building the theme QSS, so first paint already uses them.
    _s = AppSettings()
    from app.services.helicon_service import bootstrap_helicon_path_env
    bootstrap_helicon_path_env(_s)
    set_typography(scale=_s.ui_font_scale, family=_s.ui_font_family)
    # Pin the default font to an installed CJK family BEFORE any widget is
    # built — otherwise first-paint layout uses Qt's CJK-less default ("Ubuntu"
    # on Linux), causing the startup text-overlap and garbled glyphs.
    apply_default_font(app)
    startup_mark("fonts-ready")

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
    from app.config.memory_profile import apply_memory_profile, is_low_memory_machine
    if _should_default_performance_mode(
        is_wsl=_is_wsl,
        platform=sys.platform,
        low_memory=is_low_memory_machine(),
        setting_present=_s._qs.contains("appearance/performance_mode"),
    ):
        _s.performance_mode = True
    _fx.PERFORMANCE_MODE = _s.performance_mode
    from app.config import icons as _icons
    _icons.set_lightweight_mode(_s.performance_mode)
    apply_memory_profile(performance_mode=_s.performance_mode)
    app.setStyleSheet(apply_theme(_s.current_theme))
    startup_mark("theme-ready")

    from PyQt6.QtGui import QPixmapCache
    from app.config.memory_profile import QPIXMAP_CACHE_KB
    QPixmapCache.setCacheLimit(QPIXMAP_CACHE_KB)

    # ── App context (shared state + DI container) ─────────────────────
    ctx = AppContext()
    startup_mark("context-ready")

    # ── Collaboration service (P2P mDNS + FastAPI) ────────────────────
    # Lazy: CollabService is created only when collaboration is enabled
    # (or the user opens collab settings).  Saves startup RAM on 2 GB PCs.
    try:
        if ctx.settings.collab_enabled and ctx.settings.team_code:
            svc = ctx.ensure_collab_service()
            if svc is not None:
                svc.start(
                    project_name=ctx.settings.last_project_dir or "",
                    group_code=ctx.settings.team_code,
                    project_dir=ctx.settings.last_project_dir or "",
                )
    except Exception:  # noqa: BLE001
        pass  # fastapi/uvicorn not installed or network unavailable

    # ── Main window ───────────────────────────────────────────────────
    win = MainWindow(ctx)
    startup_mark("window-shell-ready")
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

    # Register navigation metadata only; individual pages import on first open.
    for view_cls in ALL_VIEW_SPECS:
        win.register_view(view_cls)
    startup_mark("navigation-ready")

    # 启动自动恢复上次项目——免得每次重启都回到 "(未选)" 空项目,用户得重选。
    _restore_last_project(ctx, win)

    # Always paint the lightweight shell first. Building a heavy view while the
    # native Windows window was hidden avoided one flash, but blocked the GUI
    # thread long enough to show a system-wide busy cursor and feel like the
    # desktop had frozen. The stable placeholder is preferable to a blocked PC.
    # 用户同一需求累计（本线程 6 次，2026-07-13）：
    # 1. 启动窗口明显过大；2. 要求遵循现代软件通用设计且不要散落硬编码；
    # 3. 明确默认窗口应为屏幕可用区域 80%；4. 确认只在首次启动使用 80%，
    #    以后恢复用户手动调整的尺寸；5. 实机核对发现启动后仅 160×28 且被最小化；
    # 6. 只修启动窗口，不得连带修改其它页面或业务。不得改回无条件最大化。
    win.restore_state(
        activate_last_view=False,
        restore_window_layout=True,
        defer_initial_view=True,
    )
    # A non-empty QByteArray is not proof that Qt accepted it. Corrupt or stale
    # geometry must fall back to the same centered first-start placement.
    has_saved_geometry = bool(
        getattr(win, "_window_geometry_restored", False)
    )
    startup_mark("state-restored")
    # WSLg multi-monitor ordering is unstable across boots and Windows display
    # changes.  The old nearest-to-(0,0) rule often opened the app on a monitor
    # the user was not looking at.  Prefer Qt primary, then cursor screen, and
    # force a second delayed raise to absorb WM races.
    target = _startup_target_screen(app)
    if not _HEADLESS_SMOKE:
        _safe_stderr_print(f"启动窗口目标屏幕: {_screen_label(target)}")
    placement = _show_main_window_at_startup(
        win, app, target, has_saved_geometry=has_saved_geometry
    )
    startup_mark("window-shown")
    if not _HEADLESS_SMOKE:
        _safe_stderr_print(f"启动窗口放置策略: {placement}")
        # WSLg occasionally loses the first focus request.  Native Windows does
        # not need the rescue; repeating raise/activate/alert there causes
        # visible full-window flashes with remote/virtual display drivers.
        if _is_wsl:
            QTimer.singleShot(250, lambda: _ensure_main_window_visible(win, app, target))
            QTimer.singleShot(1000, lambda: _ensure_main_window_visible(win, app, target))

    if _HEADLESS_SMOKE:
        app.processEvents()
        _safe_stderr_print("offscreen 启动冒烟通过：主窗口已构造完成。")
        return 0

    # Windows can apply the process startup show-command after Qt's synchronous
    # showNormal(), putting a correctly placed window back into SW_SHOWMINIMIZED.
    # Re-check once after the native event loop starts. This is scoped strictly
    # to window placement and does not rebuild or activate any page.
    if sys.platform == "win32" and not _is_wsl:
        QTimer.singleShot(
            250,
            lambda: _stabilize_main_window_after_show(
                win,
                target,
                use_adaptive_geometry=not has_saved_geometry,
            ),
        )

    # 对标 VS Code/Cursor：启动后延迟做一次后台静默检查更新（发现未跳过的新版本才提示）。
    try:
        win.start_background_update_check()
    except Exception:  # noqa: BLE001
        pass

    return app.exec()


if __name__ == "__main__":
    _bootstrap_cli_runtime()
    sys.exit(main())
