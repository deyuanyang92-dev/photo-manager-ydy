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
    # Apply BEFORE any widget/view is built — the UI is translated once at
    # construction time (restart-to-apply). tr() then resolves to this language.
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

    # Register all 14 module views
    for view_cls in ALL_VIEWS:
        win.register_view(view_cls)

    # Restore last window state + nav selection
    win.restore_state()
    # WSLg exposes one X screen per Windows monitor, but Qt's primaryScreen() is
    # NOT the user's real (Windows) primary — here it picks rdp-0 at x=1920 (the
    # right-hand monitor) while the Windows primary is the screen at the origin.
    # Pinning to Qt-primary therefore throws the window onto a monitor the user
    # isn't watching -> looks "won't open". Pick the screen nearest (0,0) instead:
    # the Windows primary always sits at the virtual-desktop origin.
    screens = app.screens() or ([app.primaryScreen()] if app.primaryScreen() else [])
    target = None
    if screens:
        target = min(screens, key=lambda s: (abs(s.geometry().x()) + abs(s.geometry().y())))
    if target is not None:
        win.setGeometry(target.availableGeometry())
    win.showMaximized()   # open full-screen so the columns get room to breathe
    if not _HEADLESS_SMOKE:
        # offscreen plugin warns "does not support raise()"; only needed for a
        # real window manager anyway (pull the window to the front + focus it).
        win.raise_()
        win.activateWindow()

    if _HEADLESS_SMOKE:
        app.processEvents()
        print("offscreen 启动冒烟通过：主窗口已构造完成。", file=sys.stderr)
        return 0

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
