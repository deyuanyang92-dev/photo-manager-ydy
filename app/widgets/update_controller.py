"""update_controller.py — 独立的软件更新流程编排（对标 VS Code / Cursor / Chrome）。

把「检查 / 下载 / 校验 / 替换重启」的 UI 编排从 MainWindow 抽出来，自包含。对外暴露：

    check_for_updates(window)              # 手动：设置→检查更新 / 顶栏更新按钮
    start_background_update_check(window)  # 启动后延迟静默检查 + 之后定时轮询
    apply_pending_update_on_exit(window)   # 退出时应用「稍后安装」挂起的更新

对标专业软件的能力：
  • 后台静默检查 + 每隔数小时定时轮询，发现新版本才非模态提示。
  • 「跳过此版本」——记住后不再提醒（QSettings: update/skip_version）。
  • **更新日志（changelog）**：提示框展示 GitHub release 正文。
  • **静默后台下载 + 重启后安装**：下载在后台完成，提示「立即重启安装 / 稍后（退出时安装）」，
    不打断当前工作。
  • **签名校验**：下载后由 update_service 用内嵌 Ed25519 公钥校验来源真实性（配置公钥后强制）。
  • **提权应用**：安装目录不可写（Program Files）时，替换脚本以管理员身份运行。
  • **用户资料保留**：由 update_service 的备份/恢复 + 目录外数据隔离保证。

真正的下载/校验/解压/替换逻辑都在 app/services/update_service.py，本模块只做编排。
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QObject, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QMessageBox,
    QProgressDialog,
    QWidget,
)

from app.config.i18n import tr
from app.config.version import APP_VERSION

_SKIP_VERSION_KEY = "update/skip_version"
_AUTO_CHECK_KEY = "update/auto_check"
_BACKGROUND_DELAY_MS = 4000
_PERIODIC_INTERVAL_MS = 6 * 60 * 60 * 1000  # 每 6 小时轮询一次
_CHANGELOG_MAX_CHARS = 1500


class _UpdateWorker(QObject):
    """后台线程：mode='check' 只查版本；mode='download' 下载并准备指定发布。"""

    checked = pyqtSignal(object, bool)   # (ReleaseInfo, is_newer)
    progress = pyqtSignal(int, str)
    ready = pyqtSignal(object)           # PreparedUpdate
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, current_version: str, *, mode: str, release=None) -> None:
        super().__init__()
        self._current_version = current_version
        self._mode = mode
        self._release = release

    def run(self) -> None:
        try:
            from app.services import update_service

            if self._mode == "check":
                release = update_service.fetch_latest_release()
                newer = update_service.is_newer_version(
                    release.tag_name, self._current_version
                )
                self.checked.emit(release, newer)
                return

            # mode == "download"
            def _on_download(done: int, total: int) -> None:
                if total > 0:
                    pct = min(95, int(done * 95 / total))
                else:
                    pct = 20
                self.progress.emit(pct, "正在下载更新包...")

            self.progress.emit(5, "正在下载更新包...")
            prepared = update_service.prepare_update(
                self._release, progress_cb=_on_download
            )
            self.progress.emit(100, "更新包已就绪。")
            self.ready.emit(prepared)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class UpdateController(QObject):
    """拥有更新线程/进度框/提示框的生命周期，避免污染 MainWindow。"""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self._window = window
        self._thread: Optional[QThread] = None
        self._worker: Optional[_UpdateWorker] = None
        self._progress: Optional[QProgressDialog] = None
        self._notify_box: Optional[QMessageBox] = None
        self._pending = None            # PreparedUpdate，选择「稍后安装」时挂起
        self._silent = False
        self._periodic: Optional[QTimer] = None

    # ── 公共入口 ──────────────────────────────────────────────────────────

    def check_manual(self) -> None:
        """用户主动检查更新。"""
        self._begin_check(silent=False)

    def check_background(self) -> None:
        """后台静默检查：仅在发现（未跳过的）新版本时提示。"""
        if not self._auto_check_enabled():
            return
        self._begin_check(silent=True)

    def start_periodic(self) -> None:
        """启动定时轮询（对标专业软件每隔数小时自动查一次）。"""
        if self._periodic is not None:
            return
        timer = QTimer(self)
        timer.setInterval(_PERIODIC_INTERVAL_MS)
        timer.timeout.connect(self.check_background)
        timer.start()
        self._periodic = timer

    def has_pending_update(self) -> bool:
        return self._pending is not None

    def apply_pending_update(self) -> None:
        """退出时调用：若有「稍后安装」的更新，启动替换脚本。"""
        prepared = self._pending
        if prepared is None:
            return
        self._pending = None
        self._launch(prepared)

    # ── 检查阶段 ──────────────────────────────────────────────────────────

    def _begin_check(self, *, silent: bool) -> None:
        if self._thread is not None and self._thread.isRunning():
            if not silent:
                self._status(tr("更新正在进行中，请稍候..."))
            return
        self._silent = silent

        progress: Optional[QProgressDialog] = None
        if not silent:
            progress = self._make_progress(tr("正在检查更新..."))
        self._progress = progress

        worker = _UpdateWorker(APP_VERSION, mode="check")
        worker.checked.connect(self._on_checked)
        worker.failed.connect(self._on_failed)
        self._run_worker(worker)

    def _on_checked(self, release, is_newer: bool) -> None:
        from app.services import update_service

        self._close_progress()
        if not is_newer:
            if not self._silent:
                QMessageBox.information(
                    self._window,
                    tr("软件更新"),
                    tr("当前已是最新版本。\n\n当前版本：{}\nGitHub 最新：{}").format(
                        APP_VERSION, getattr(release, "tag_name", "") or APP_VERSION,
                    ),
                )
            return

        tag = getattr(release, "tag_name", "") or "?"
        can_self = update_service.can_self_update()

        # 静默：发现新版本 → 非模态通知（尊重「跳过此版本」）
        if self._silent:
            if tag and tag == self._skipped_version():
                return
            self._notify_available(release, can_self)
            return

        # 手动：不能自替换（源码环境）→ 引导手动下载
        if not can_self:
            self._show_manual_download(release)
            return

        # 手动 + 可自替换 → 确认（带更新日志）后下载
        if self._confirm_update(release):
            self._start_download(release, show_progress=True)

    # ── 下载阶段 ──────────────────────────────────────────────────────────

    def _start_download(self, release, *, show_progress: bool) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._status(tr("更新正在进行中，请稍候..."))
            return
        self._silent = not show_progress
        progress = self._make_progress(tr("正在下载更新...")) if show_progress else None
        self._progress = progress
        if not show_progress:
            self._status(tr("正在后台下载更新 {}...").format(
                getattr(release, "tag_name", "") or ""))

        worker = _UpdateWorker(APP_VERSION, mode="download", release=release)
        worker.progress.connect(self._on_progress)
        worker.ready.connect(self._on_ready)
        worker.failed.connect(self._on_failed)
        self._run_worker(worker)

    def _on_progress(self, value: int, text: str) -> None:
        if self._progress is not None:
            self._progress.setLabelText(tr(text))
            self._progress.setValue(value)

    def _on_ready(self, prepared) -> None:
        self._close_progress()
        self._offer_install(prepared)

    # ── 应用阶段 ──────────────────────────────────────────────────────────

    def _offer_install(self, prepared) -> None:
        tag = getattr(getattr(prepared, "release", None), "tag_name", "") or ""
        box = QMessageBox(self._window)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(tr("更新已就绪"))
        box.setText(
            tr("新版本 {} 已下载并校验通过。\n\n"
               "现在重启安装，还是下次退出程序时自动安装？\n"
               "（更新会保留你的项目与设置）").format(tag or "?")
        )
        now_btn = box.addButton(tr("立即重启安装"), QMessageBox.ButtonRole.AcceptRole)
        later_btn = box.addButton(tr("退出时安装"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is now_btn:
            self._launch(prepared, quit_after=True)
        elif box.clickedButton() is later_btn:
            self._pending = prepared
            self._status(tr("更新将在退出程序时自动安装。"))

    def _launch(self, prepared, *, quit_after: bool = False) -> None:
        from app.services import update_service

        try:
            update_service.launch_update_script(
                prepared.script_path,
                elevate=bool(getattr(prepared, "requires_elevation", False)),
            )
        except Exception as exc:  # noqa: BLE001
            self._on_failed(str(exc))
            return
        tag = getattr(getattr(prepared, "release", None), "tag_name", "") or ""
        self._status(tr("正在升级到 {}...").format(tag))
        if quit_after:
            QApplication.quit()

    # ── 提示框 ────────────────────────────────────────────────────────────

    def _confirm_update(self, release) -> bool:
        box = QMessageBox(self._window)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(tr("软件更新"))
        box.setText(
            tr("发现新版本 {}（当前 {}）。\n\n"
               "将下载并更新，完成后可立即或退出时安装（保留你的项目与设置）。\n"
               "是否继续？").format(
                getattr(release, "tag_name", "") or "?", APP_VERSION)
        )
        self._attach_changelog(box, release)
        yes = box.addButton(tr("下载并更新"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("取消"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is yes

    def _notify_available(self, release, can_self: bool) -> None:
        from app.services import update_service

        tag = getattr(release, "tag_name", "") or "?"
        page = getattr(release, "page_url", "") or update_service.LATEST_RELEASE_PAGE

        box = QMessageBox(self._window)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(tr("发现新版本"))
        if can_self:
            box.setText(
                tr("发现新版本 {}（当前 {}）。\n\n"
                   "现在下载并更新？会保留你的项目与设置。").format(tag, APP_VERSION)
            )
            action_btn = box.addButton(tr("现在更新"), QMessageBox.ButtonRole.AcceptRole)
        else:
            box.setText(
                tr("发现新版本 {}（当前 {}）。\n\n"
                   "当前是源码/开发环境，请手动下载更新。").format(tag, APP_VERSION)
            )
            action_btn = box.addButton(tr("打开下载页"), QMessageBox.ButtonRole.AcceptRole)
        self._attach_changelog(box, release)
        skip_btn = box.addButton(tr("跳过此版本"), QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(tr("稍后"), QMessageBox.ButtonRole.RejectRole)
        box.setModal(False)

        def _clicked(_btn) -> None:
            clicked = box.clickedButton()
            if clicked is skip_btn:
                self._set_skipped_version(tag)
            elif clicked is action_btn:
                if can_self:
                    self._start_download(release, show_progress=False)
                else:
                    QDesktopServices.openUrl(QUrl(page))

        box.buttonClicked.connect(_clicked)
        box.finished.connect(lambda _r: setattr(self, "_notify_box", None))
        self._notify_box = box
        box.show()

    def _show_manual_download(self, release) -> None:
        from app.services import update_service

        tag = getattr(release, "tag_name", "") or "?"
        page = getattr(release, "page_url", "") or update_service.LATEST_RELEASE_PAGE
        box = QMessageBox(self._window)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(tr("软件更新"))
        box.setText(
            tr("发现新版本 {}（当前 {}）。\n\n"
               "当前是源码/开发环境运行，不能自动替换程序。\n"
               "请手动下载 Windows 便携包更新。").format(tag, APP_VERSION)
        )
        self._attach_changelog(box, release)
        open_btn = box.addButton(tr("打开下载页"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("关闭"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl(page))

    def _on_failed(self, message: str) -> None:
        from app.services import update_service

        self._close_progress()
        if self._silent:
            self._status(tr("检查/下载更新失败（网络或 GitHub 暂不可达）"))
            return
        QMessageBox.warning(
            self._window,
            tr("软件更新失败"),
            tr("未能完成更新：\n{}\n\n"
               "可稍后重试，或手动打开下载页获取最新版：\n{}").format(
                message, update_service.LATEST_RELEASE_PAGE,
            ),
        )

    # ── 线程与小工具 ──────────────────────────────────────────────────────

    def _run_worker(self, worker: _UpdateWorker) -> None:
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None

    def _make_progress(self, text: str) -> QProgressDialog:
        progress = QProgressDialog(text, "", 0, 100, self._window)
        progress.setWindowTitle(tr("软件更新"))
        progress.setCancelButton(None)
        progress.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        progress.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.show()
        return progress

    def _close_progress(self) -> None:
        if self._progress is not None:
            self._progress.close()
            self._progress = None

    def _attach_changelog(self, box: QMessageBox, release) -> None:
        body = str(getattr(release, "body", "") or "").strip()
        if not body:
            return
        if len(body) > _CHANGELOG_MAX_CHARS:
            body = body[:_CHANGELOG_MAX_CHARS] + "\n…"
        box.setDetailedText(tr("更新内容：\n\n{}").format(body))

    def _status(self, text: str) -> None:
        bar = getattr(self._window, "statusBar", None)
        if callable(bar):
            try:
                bar().showMessage(text, 4000)
            except Exception:  # noqa: BLE001
                pass

    def _qs(self):
        ctx = getattr(self._window, "ctx", None)
        settings = getattr(ctx, "settings", None)
        return getattr(settings, "_qs", None)

    def _auto_check_enabled(self) -> bool:
        qs = self._qs()
        if qs is None:
            return True
        return str(qs.value(_AUTO_CHECK_KEY, "true")).lower() in {"true", "1", "yes"}

    def _skipped_version(self) -> str:
        qs = self._qs()
        if qs is None:
            return ""
        return str(qs.value(_SKIP_VERSION_KEY, "") or "")

    def _set_skipped_version(self, tag: str) -> None:
        qs = self._qs()
        if qs is not None and tag:
            qs.setValue(_SKIP_VERSION_KEY, tag)


# ── 便捷函数：让 MainWindow 只需一行调用 ─────────────────────────────────


def _get_controller(window: QWidget) -> UpdateController:
    controller = getattr(window, "_update_controller", None)
    if controller is None:
        controller = UpdateController(window)
        window._update_controller = controller  # type: ignore[attr-defined]
    return controller


def check_for_updates(window: QWidget) -> None:
    """手动检查更新（顶栏按钮 / 设置→检查更新）。"""
    _get_controller(window).check_manual()


def start_background_update_check(window: QWidget) -> None:
    """启动后延迟触发后台静默检查，并开启定时轮询。"""
    controller = _get_controller(window)

    def _kick() -> None:
        controller.start_periodic()
        controller.check_background()

    QTimer.singleShot(_BACKGROUND_DELAY_MS, _kick)


def apply_pending_update_on_exit(window: QWidget) -> None:
    """退出时应用「稍后安装」挂起的更新（若有）。"""
    controller = getattr(window, "_update_controller", None)
    if controller is not None:
        controller.apply_pending_update()
