"""WoRMS service, batch job, match, and review workflow for taxonomy."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
# NOTE: ``Any`` is used for typed record/workflow payloads passed through
# worker/result helpers; keep this import explicit for static checks and IDEs.

from PyQt6.QtWidgets import QDialog, QMessageBox

from app.services.worms_service import WormsService
from app.views.taxonomy_dialogs import _TaxonReviewDialog, _WormsMatchDialog
from app.views.taxonomy_workers import _WormsJobWorker


class TaxonomyWormsWorkflowMixin:
    # ── WoRMS service helpers ─────────────────────────────────────────────────

    def _worms_data_dir(self) -> Path:
        project_dir = getattr(self.ctx, "current_project_dir", None)
        if project_dir: return Path(project_dir) / "_data"
        return Path.home() / ".photo_workbench" / "data"

    def _ensure_worms_svc(self) -> Optional[WormsService]:
        if self._worms_svc is not None: return self._worms_svc
        data_dir = self._worms_data_dir(); data_dir.mkdir(parents=True, exist_ok=True)
        self._worms_svc = WormsService(cache_path=str(data_dir / "worms_cache.json"), jobs_path=str(data_dir / "worms_jobs.json"))
        return self._worms_svc

    # ── WoRMS match / review / resolve ────────────────────────────────────────

    # ── WoRMS update (mirrors startTaxonomyWormsJob in app.js) ───────────────

    def _on_worms_update(self, selected_only: bool) -> None:
        if self._job_worker is not None and self._job_worker.isRunning():
            QMessageBox.information(self, "WoRMS 更新", "已有 WoRMS 任务在运行，请等待完成或在进度区暂停。")
            return
        record_ids = self._worms_update_record_ids(selected_only)
        if not record_ids:
            QMessageBox.information(self, "WoRMS 更新", "没有可更新的分类条目。")
            return
        source = "selected" if selected_only and not self._select_all_filtered else "filtered"
        scope = f"已选 {len(record_ids)} 条" if source == "selected" else f"筛选结果 {len(record_ids)} 条"
        confirm = QMessageBox.question(
            self, "WoRMS 更新",
            f"即将对{scope}发起 WoRMS 校验更新。\n"
            "将逐条访问 WoRMS（约每条 0.6 秒），结果记录为校验状态供审核，"
            "不会改写原始条目；可在进度条处随时暂停。\n\n是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        service = self._ensure_worms_svc()
        if service is None:
            QMessageBox.warning(self, "WoRMS 更新", "WoRMS 服务不可用。")
            return
        try:
            job = service.create_job(record_ids, source=source)
        except Exception as exc:
            QMessageBox.warning(self, "WoRMS 更新", f"创建任务失败：{exc}")
            return
        setattr(self.ctx, "pending_worms_job_id", job.id)
        self._start_job_worker(job.id)

    def _build_record_resolver(self):
        """Return a recordId→record lookup over the FULL library.

        Selections may span pages, so the resolver must see every record, not
        just the visible page.  Records without a recordId (seed rows) are not
        checkbox-selectable and are simply absent → worker treats them as stale.
        """
        if self._svc is None:
            return lambda rid: None
        total = self._svc.seed_count() + self._svc.user_count()
        rows, _ = self._svc.all_records(page=0, page_size=max(total, 1))
        index = {r.get("recordId", ""): r for r in rows if r.get("recordId")}
        return lambda rid: index.get(rid)

    def _start_job_worker(self, job_id: str) -> None:
        service = self._ensure_worms_svc()
        if service is None:
            return
        worker = _WormsJobWorker(service, job_id, self._build_record_resolver(), parent=self)
        worker.progress.connect(self._on_job_progress)
        worker.finished_job.connect(self._on_job_finished)
        worker.failed.connect(self._on_job_failed)
        self._job_worker = worker
        self._refresh_job_panel()   # show panel immediately at 0/N
        worker.start()

    def _on_job_progress(self, cursor: int, total: int, counts: dict) -> None:
        self._refresh_job_panel()

    def _on_job_finished(self, job: dict) -> None:
        self._refresh_job_panel()
        # Reload so per-row mapping status surfaces (审核 entry appears on review rows).
        self._load_page()
        if job.get("status") == "completed":
            counts = job.get("counts") or {}
            _CL = {"matched": "匹配", "renamed": "改名", "review": "待审",
                   "not_found": "未找到", "error": "错误", "stale": "跳过"}
            summary = " · ".join(f"{_CL.get(k, k)} {v}" for k, v in counts.items() if v) or "无变化"
            QMessageBox.information(self, "WoRMS 更新", f"WoRMS 校验完成：{summary}")

    def _on_job_failed(self, msg: str) -> None:
        self._refresh_job_panel()
        QMessageBox.warning(self, "WoRMS 更新", f"任务出错：{msg}")

    def _on_job_pause(self) -> None:
        service = self._ensure_worms_svc()
        if service is None:
            return
        active = next((j for j in service.list_jobs() if j.get("status") == "running"), None)
        if active:
            service.update_job_status(active["id"], "paused")   # worker exits at next tick
        self._refresh_job_panel()

    def _on_job_resume(self) -> None:
        service = self._ensure_worms_svc()
        if service is None:
            return
        paused = next((j for j in service.list_jobs() if j.get("status") == "paused"), None)
        if not paused:
            return
        service.update_job_status(paused["id"], "running")
        self._start_job_worker(paused["id"])

    def _on_job_retry(self) -> None:
        service = self._ensure_worms_svc()
        if service is None:
            return
        target = next((j for j in service.list_jobs() if (j.get("counts") or {}).get("error")), None)
        if not target:
            return
        retried = service.retry_failed_job(target["id"])
        if retried:
            self._start_job_worker(retried["id"])

    def _annotate_mappings(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        worms_svc = self._ensure_worms_svc()
        if worms_svc is None:
            return records
        try:
            mappings = worms_svc.list_mappings()
        except Exception:
            return records
        if not mappings:
            return records
        out: list[dict[str, Any]] = []
        for rec in records:
            m = mappings.get(rec.get("recordId", ""))
            if m:
                rec = dict(rec)
                rec["mappingStatus"] = m.get("status", "")
                rec["mappingCandidates"] = m.get("candidates", [])
            out.append(rec)
        return out

    def _worms_update_record_ids(self, selected_only: bool) -> list[str]:
        if self._svc is None: return []
        if selected_only and not self._select_all_filtered:
            return list(dict.fromkeys(rid for rid in self._model.checked_ids() if rid))
        source_filter = "seed" if self._view == "worms" else None
        rows, total = self._svc.all_records(source_filter=source_filter, page=0, page_size=1_000_000)
        if len(rows) < total: rows, _ = self._svc.all_records(source_filter=source_filter, page=0, page_size=max(total, 1))
        ids: list[str] = []
        for idx, rec in enumerate(rows):
            if self._filter_text and not self._record_matches_filter(rec): continue
            ids.append(self._taxonomy_record_id(rec, idx, source_filter))
        return ids

    def _taxonomy_record_id(self, rec: dict[str, Any], index: int, source_filter: Optional[str] = None) -> str:
        rid = str(rec.get("recordId") or "").strip()
        if rid: return rid
        return f"{source_filter or ('user' if str(rec.get('recordId', '')).startswith('user:') else 'seed')}:{index}"

    def _navigate_to_worms(self) -> None:
        win = self.window()
        nav = getattr(win, "navigate_to", None)
        if callable(nav): nav("worms")
        wv = getattr(win, "_views", {}).get("worms") if hasattr(win, "_views") else None
        ref = getattr(wv, "_refresh_jobs", None)
        if callable(ref): ref()

    def _on_worms_match_row(self, rec: dict[str, Any]) -> None:
        worms_svc = self._ensure_worms_svc()
        if worms_svc is None: QMessageBox.warning(self, "WoRMS", "WoRMS 服务不可用"); return
        dlg = _WormsMatchDialog(rec, worms_svc, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            result = dlg.get_result()
            if result: self._on_resolve_mapping(rec, result)

    def _on_review_worms_row(self, rec: dict[str, Any]) -> None:
        dlg = _TaxonReviewDialog(rec, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            result = dlg.get_result()
            if result: self._on_resolve_mapping(rec, result)

    def _on_resolve_mapping(self, rec: dict[str, Any], result: dict[str, Any]) -> None:
        """Apply WoRMS resolution decision (mirrors resolveTaxonMapping in app.js)."""
        worms_svc = self._ensure_worms_svc()
        if worms_svc is None: return
        record_id = rec.get("recordId", "")
        if not record_id: return
        try:
            if result.get("no_match"):
                worms_svc.resolve_mapping(record_id, None, no_match=True)
            else:
                aphia_id = result.get("aphia_id")
                worms_svc.resolve_mapping(record_id, int(aphia_id) if aphia_id else None, worms_record=result.get("worms_record") or {}, chain=result.get("chain") or [])
            QMessageBox.information(self, "WoRMS", "审核结果已保存")
        except Exception as exc:
            QMessageBox.warning(self, "WoRMS 错误", f"审核失败：{exc}")
        self._load_page()

    # ── Job panel (mirrors renderTaxonJobPanel in app.js) ─────────────────────

    def _refresh_job_panel(self) -> None:
        worms_svc = self._ensure_worms_svc()
        if worms_svc is None: self._job_panel_frame.hide(); return
        jobs = worms_svc.list_jobs()
        if not jobs: self._job_panel_frame.hide(); return
        active = next((j for j in jobs if j.get("status") in ("running", "paused")), None)
        job = active or jobs[0]
        record_ids = job.get("record_ids") or []; total = len(record_ids); cursor = job.get("cursor", 0); status = job.get("status", ""); source = job.get("source", "")
        _SL = {"running": "运行中", "paused": "已暂停", "completed": "已完成", "cancelled": "已取消"}
        self._job_title_label.setText(f"WoRMS 任务 · {'选中条目' if source == 'selected' else '筛选结果'}")
        self._job_progress_label.setText(f"{cursor} / {total} · {_SL.get(status, status)}")
        self._job_bar.setRange(0, max(total, 1)); self._job_bar.setValue(cursor)
        counts = job.get("counts") or {}
        _CL = {"matched": "匹配", "renamed": "改名", "review": "待审", "not_found": "未找到", "error": "错误", "stale": "旧缓存"}
        self._job_counts_label.setText("  ".join(f"{_CL.get(k, k)} {v}" for k, v in counts.items() if v))
        self._btn_job_pause.setVisible(status == "running"); self._btn_job_resume.setVisible(status == "paused")
        self._btn_job_retry.setVisible(bool(counts.get("error"))); self._job_panel_frame.show()
