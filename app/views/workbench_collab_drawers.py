"""workbench_collab_drawers.py — Workbench collaboration/settings drawer mixin."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import QMessageBox


class WorkbenchCollabDrawerMixin:
    """Collab file sync and right-side drawer actions for WorkbenchView."""

    # ── Collaboration file sync ──────────────────────────────────────────────

    def _collab_file_sync_peers(self) -> list:
        svc = getattr(self.ctx, "collab_service", None)
        if svc is None or not svc.is_running() or not svc.group_code:
            return []
        local_project_id = str(getattr(svc, "project_id", "") or "")
        if not local_project_id:
            return []
        return [
            peer for peer in svc.peers()
            if getattr(peer, "group_code", "") == svc.group_code
            and str(getattr(peer, "project_id", "") or "") == local_project_id
        ]

    def _on_sync_selected_uid(self, mode: str = "smart") -> None:
        uid = self._sidebar.current_uid() or self._current_uid
        if not uid:
            self._status_message("请先在左侧选择一个编号。")
            return
        self._start_collab_file_sync([uid], title=f"同步编号 {uid}", mode=mode)

    def _on_sync_project_files(self, mode: str = "smart") -> None:
        self._start_collab_file_sync(None, title="同步整个项目", mode=mode)

    def _start_collab_file_sync(self, uids: Optional[list[str]], *, title: str,
                                mode: str = "smart") -> None:
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            self._status_message("请先打开项目。")
            return
        svc = getattr(self.ctx, "collab_service", None)
        if svc is not None:
            svc.ensure_running(self.ctx)
        if svc is None or not svc.is_running() or not svc.group_code:
            QMessageBox.information(self, "照片同步", "请先启用局域网协作并设置协作组码。")
            return
        project_id = str(getattr(svc, "project_id", "") or "")
        if not project_id:
            QMessageBox.information(
                self,
                "照片同步",
                "当前项目还没有可用于照片同步的项目同步码。请重新打开项目后再同步照片。",
            )
            return
        same_group_peers = [
            peer for peer in svc.peers()
            if getattr(peer, "group_code", "") == svc.group_code
        ]
        peers = self._collab_file_sync_peers()
        if not peers:
            if same_group_peers:
                QMessageBox.information(
                    self,
                    "照片同步",
                    "当前在线设备没有使用同一个项目同步码。\n\n"
                    "任务状态可以跨项目查看；照片/TIF/ZIP 只允许同项目同步码设备同步。\n"
                    "请到协作中心点击“绑定同一项目”，由确认是同一项目的一台电脑分享项目码。",
                )
            else:
                QMessageBox.information(self, "照片同步", "没有同组在线设备，无法同步照片。")
            return
        current_worker = getattr(self, "_collab_file_sync_worker", None)
        if current_worker is not None and current_worker.isRunning():
            self._status_message("照片同步正在进行中。")
            return
        if mode == "overwrite":
            ret = QMessageBox.warning(
                self,
                "强制覆盖本机文件",
                "强制覆盖会用队友设备上的同名文件替换本机文件。\n\n"
                "本机旧文件会先备份到 _data/sync-conflicts/，不会直接删除。\n"
                "确定继续吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return

        from app.workers.collab_file_sync_worker import CollabFileSyncWorker

        progress = None

        worker = CollabFileSyncWorker(
            project_dir=project_dir,
            peers=peers,
            group_code=svc.group_code,
            project_id=project_id,
            uids=uids,
            mode=mode,
            max_workers=4,
            parent=self,
        )
        self._collab_file_sync_worker = worker
        self._collab_file_sync_progress = progress
        task_key = f"collab-sync:{title}:{id(worker)}"
        self._workflow_notice(
            f"{title}：准备同步",
            f"正在连接 {len(peers)} 台同项目设备；同步在后台运行。",
            state="busy",
            force_show=True,
            task_key=task_key,
        )

        def _on_progress(current: int, total: int, rel: str) -> None:
            if progress is not None:
                progress.setMaximum(max(1, total))
                progress.setValue(min(current, max(1, total)))
                progress.setLabelText(f"正在同步 {current}/{total}\n{rel}")
            self._workflow_notice(
                f"{title}：正在同步",
                f"正在同步 {current}/{total}：{rel}",
                state="busy",
                task_key=task_key,
            )

        def _finish(summary) -> None:
            if progress is not None:
                progress.close()
            self._collab_file_sync_worker = None
            self._collab_file_sync_progress = None
            try:
                self._refresh_monitor()
                self._sidebar.refresh()
                if self._current_uid:
                    self._on_show_current_results()
            except Exception:
                pass
            msg = (
                f"照片同步完成：下载 {summary.downloaded}，跳过 {summary.skipped}，"
                f"冲突 {summary.conflicts}，失败 {summary.failed}"
                f"，同步码不同跳过 {getattr(summary, 'incompatible_peers', 0)}。"
            )
            state = "error" if summary.conflicts or summary.failed else "success"
            self._workflow_notice(
                f"{title}完成",
                msg,
                state=state,
                task_key=task_key,
            )
            self._status_message(msg)
            if summary.conflicts or summary.failed:
                detail = ""
                if summary.conflict_paths:
                    detail += "冲突文件：\n" + "\n".join(summary.conflict_paths[:12])
                    if len(summary.conflict_paths) > 12:
                        detail += f"\n... 还有 {len(summary.conflict_paths) - 12} 个"
                if summary.failed_paths:
                    if detail:
                        detail += "\n\n"
                    detail += "失败文件：\n" + "\n".join(summary.failed_paths[:12])
                    if len(summary.failed_paths) > 12:
                        detail += f"\n... 还有 {len(summary.failed_paths) - 12} 个"
                QMessageBox.warning(self, "照片同步完成但有问题", detail or msg)

        def _fail(message: str) -> None:
            if progress is not None:
                progress.close()
            self._collab_file_sync_worker = None
            self._collab_file_sync_progress = None
            self._workflow_notice(
                f"{title}失败",
                message or "未知错误",
                state="error",
                task_key=task_key,
            )
            QMessageBox.warning(self, "照片同步失败", message or "未知错误")

        worker.progress.connect(_on_progress)
        worker.finished_summary.connect(_finish)
        worker.failed.connect(_fail)
        worker.finished.connect(worker.deleteLater)
        worker.start()
        if progress is not None:
            progress.show()

    def _collab_operator(self) -> str | None:
        """Current operator name (for task assignee), read safely from settings."""
        try:
            raw = self.ctx.settings._qs.value("user/current_user", "")
            name = str(raw).strip()
            return name or None
        except Exception:
            return None

    def _on_open_collab_panel(self) -> None:
        """Show collab panel drawer positioned at the right edge."""
        self._collab_panel.refresh()
        try:
            win_rect = self.rect()
            self._collab_scrim.setGeometry(win_rect)
            self._collab_scrim.show()
            self._collab_scrim.raise_()
            p = self._collab_panel
            p.setGeometry(
                win_rect.right() - p.width(), 0,
                p.width(), win_rect.height()
            )
            p.show()
            p.raise_()
        except Exception:
            self._collab_panel.show()

    def _close_collab_panel(self) -> None:
        """Dismiss the collab panel and its backdrop scrim."""
        self._collab_scrim.hide()
        self._collab_panel.close_panel()

    def _refresh_collab_card(self) -> None:
        """Refresh the right-rail collab card when tasks change."""
        if self._current_uid:
            self._collab_card.load_specimen(self._current_uid)

    def _on_open_settings(self) -> None:
        """Show project settings drawer positioned at the right edge."""
        self._settings_drawer.refresh()
        try:
            win_rect = self.rect()
            # Backdrop scrim covers the whole view, drawer sits on top of it.
            self._settings_scrim.setGeometry(win_rect)
            self._settings_scrim.show()
            self._settings_scrim.raise_()
            dw = self._settings_drawer
            dw.setGeometry(
                win_rect.right() - dw.width(), 0,
                dw.width(), win_rect.height()
            )
            dw.show()
            dw.raise_()
        except Exception:
            self._settings_drawer.show()

    def _close_settings(self) -> None:
        """Dismiss the settings drawer and its backdrop scrim."""
        self._settings_scrim.hide()
        self._settings_drawer._on_close()


