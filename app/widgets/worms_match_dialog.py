"""worms_match_dialog.py — WoRMS 批量匹配 (Match Taxa) 向导.

复刻 WoRMS 官网 Match Taxa 工具并超越之：导入 Excel/CSV/TXT 学名表 →
用 TAXAMATCH (AphiaRecordsByMatchNames) 批量匹配 → 人工消歧 →
导出保留全部原列 + 追加 WoRMS 列的标注文件。

内核：WormsService.match_names（本地按名缓存、≤50 分块、无行数上限）。
本对话框只读文件、写文件，不写项目 DB。

逻辑方法（load_file / name_list / selected_append_cols / set_results /
resolve_row / export）与 UI 解耦，便于 offscreen 单测。

复用：
  - coord_import_service.read_table  —— Excel/CSV/TXT 解析
  - export_service.export_annotated_*  —— 标注文件写入 + 列目录
  - utils.ui  —— 文件对话框 / 消息框（强制 DontUseNativeDialog）
  - 进度走 QThread(WormsMatchWorker)，比照 supp_compression_worker。
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressDialog,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.config.i18n import current_language, tr
from app.services import coord_import_service as cis
from app.services.export_service import (
    MATCH_APPEND_COLUMNS,
    export_annotated_csv,
    export_annotated_xlsx,
)
from app.utils import ui

_NONE = "—无—"

# resolution → (徽章文字, 颜色)
_RES_BADGE = {
    "matched":   ("✓ 已匹配", "#027a48"),
    "near":      ("● 待确认", "#b54708"),
    "ambiguous": ("◆ 多候选", "#175cd3"),
    "none":      ("✗ 无匹配", "#b42318"),
}

_NAME_HINTS = ["学名", "拉丁", "scientific", "species", "taxon", "物种", "name"]
_AUTHOR_HINTS = ["命名人", "作者", "权威", "author", "authority"]

# 输出列表头语言（界面标签 → export lang code）
_OUTPUT_LANG_OPTIONS = [("中文", "zh"), ("English", "en"), ("双语 / Bilingual", "both")]

# Match-upto 阶元（界面中文 → WoRMS rank；种=ScientificName 不过滤）
_MATCH_UPTO = [
    ("种 (ScientificName)", None),
    ("属 (Genus)", "Genus"),
    ("科 (Family)", "Family"),
    ("目 (Order)", "Order"),
    ("纲 (Class)", "Class"),
    ("门 (Phylum)", "Phylum"),
    ("界 (Kingdom)", "Kingdom"),
]


# ── Background match worker ───────────────────────────────────────────────────

class WormsMatchWorker(QThread):
    """Run match_names off the UI thread; optionally enrich the full chain."""

    progress = pyqtSignal(int, int)   # (done, total)
    finished = pyqtSignal(object)     # list[dict]
    failed = pyqtSignal(str)

    def __init__(
        self,
        service,
        names: list[str],
        *,
        marine_only: bool = False,
        auto_accept_near: bool = False,
        fetch_chain: bool = False,
        limit_taxon: Optional[str] = None,
        match_authority: bool = False,
        authorities: Optional[list[str]] = None,
        match_rank: Optional[str] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._names = list(names)
        self._marine_only = bool(marine_only)
        self._auto_accept_near = bool(auto_accept_near)
        self._fetch_chain = bool(fetch_chain)
        self._limit_taxon = limit_taxon or None
        self._match_authority = bool(match_authority)
        self._authorities = list(authorities) if authorities else None
        self._match_rank = match_rank or None

    def run(self) -> None:  # noqa: D401 — runs in a separate thread
        try:
            results = self._service.match_names(
                self._names,
                marine_only=self._marine_only,
                auto_accept_near=self._auto_accept_near,
                limit_taxon=self._limit_taxon,
                match_authority=self._match_authority,
                authorities=self._authorities,
                match_rank=self._match_rank,
                progress_cb=lambda d, t: self.progress.emit(d, t),
            )
            if self._fetch_chain:
                accepted = [r for r in results if r.get("best")]
                total = len(accepted)
                for i, r in enumerate(accepted, start=1):
                    best = r["best"]
                    aid = best.get("valid_AphiaID") or best.get("AphiaID")
                    try:
                        chain = self._service.flatten_classification(
                            self._service.classification(int(aid))
                        )
                        r["chain"] = chain
                    except Exception:
                        pass
                    self.progress.emit(i, total)
            self.finished.emit(results)
        except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
            self.failed.emit(str(exc))


# ── Candidate picker (manual disambiguation) ──────────────────────────────────

class _CandidatePickerDialog(QDialog):
    """Pick the right WoRMS record for an ambiguous / near / no-match row."""

    def __init__(self, result: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._result = result
        self.chosen: Optional[dict] = None
        self.mark_none = False
        self.setWindowTitle(tr("选择 WoRMS 候选"))
        self.resize(640, 420)
        self._build_ui()
        ui.center_on(self, parent)

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        head = QLabel(tr("输入名：{}").format(self._result.get("input", "")))
        head.setStyleSheet("font-size:14px;font-weight:600;")
        v.addWidget(head)

        self._list = QListWidget()
        for c in (self._result.get("candidates") or []):
            taxon = " > ".join(
                str(c.get(k, "")) for k in ("class", "order", "family") if c.get(k)
            )
            label = (
                f"{c.get('scientificname', '')}  {c.get('authority', '')}\n"
                f"    {tr('命中')}:{c.get('match_type', '')}  {tr('状态')}:{c.get('status', '')}  "
                f"AphiaID:{c.get('AphiaID', '')}\n    {taxon}"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, c)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)
        else:
            self._list.addItem(QListWidgetItem(tr("（无候选——可标记为无匹配）")))
        self._list.itemDoubleClicked.connect(lambda *_: self._accept_choice())
        v.addWidget(self._list, 1)

        bar = QHBoxLayout()
        btn_none = QPushButton(tr("标记为无匹配"))
        btn_none.clicked.connect(self._mark_none)
        bar.addWidget(btn_none)
        bar.addStretch()
        cancel = QPushButton(tr("取消"))
        cancel.clicked.connect(self.reject)
        adopt = QPushButton(tr("采用"))
        adopt.setDefault(True)
        adopt.clicked.connect(self._accept_choice)
        bar.addWidget(cancel)
        bar.addWidget(adopt)
        v.addLayout(bar)

    def _accept_choice(self) -> None:
        item = self._list.currentItem()
        c = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not c:
            ui.warn(self, tr("选择"), tr("没有可采用的候选。可点「标记为无匹配」。"))
            return
        self.chosen = c
        self.accept()

    def _mark_none(self) -> None:
        self.mark_none = True
        self.accept()


# ── Main wizard ───────────────────────────────────────────────────────────────

class WormsMatchDialog(QDialog):
    """WoRMS 批量匹配向导。文件进 → 标注文件出，不写项目 DB。"""

    def __init__(self, service, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._service = service
        self._headers: list[str] = []
        self._rows: list[dict] = []
        self._results: list[dict] = []
        self._append_checks: dict[str, QCheckBox] = {}
        self._file_path: Optional[str] = None
        self._worker: Optional[WormsMatchWorker] = None
        self._progress: Optional[QProgressDialog] = None
        self.setWindowTitle(tr("WoRMS 批量匹配 (Match Taxa)"))
        self.resize(940, 660)
        self._build_ui()
        ui.center_on(self, parent)

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        self._stack = QStackedWidget()
        v.addWidget(self._stack, 1)
        self._stack.addWidget(self._build_config_page())   # index 0
        self._stack.addWidget(self._build_review_page())   # index 1

    def _build_config_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)

        top = QHBoxLayout()
        btn_file = QPushButton(tr("选择文件 (Excel/CSV/TXT)…"))
        btn_file.clicked.connect(self._pick_file)
        self._file_lbl = QLabel(tr("未选择文件"))
        self._file_lbl.setStyleSheet("color:#667085;")
        top.addWidget(btn_file)
        top.addWidget(self._file_lbl, 1)
        v.addLayout(top)

        hint = QLabel(tr(
            "导入一份含拉丁学名的表格，用 WoRMS 的 TAXAMATCH 算法批量匹配。"
            "原表所有列都会保留，按下方勾选追加 WoRMS 列。"
        ))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#667085;")
        v.addWidget(hint)

        # 列映射 + 匹配选项
        opt = QGridLayout()
        opt.addWidget(QLabel(tr("学名列")), 0, 0)
        self._name_combo = QComboBox()
        self._name_combo.addItem(_NONE)
        self._name_combo.currentIndexChanged.connect(lambda *_: self._refresh_preview())
        opt.addWidget(self._name_combo, 0, 1)

        opt.addWidget(QLabel(tr("命名人列（可选）")), 0, 2)
        self._author_combo = QComboBox()
        self._author_combo.addItem(_NONE)
        opt.addWidget(self._author_combo, 0, 3)

        self._header_cb = QCheckBox(tr("首行为表头"))
        self._header_cb.setChecked(True)
        self._header_cb.toggled.connect(self._on_header_toggled)
        opt.addWidget(self._header_cb, 1, 0, 1, 2)

        self._marine_cb = QCheckBox(tr("仅海洋物种 (marine_only)"))
        self._marine_cb.setChecked(False)
        opt.addWidget(self._marine_cb, 1, 2, 1, 2)

        self._authority_cb = QCheckBox(tr("匹配命名人 (Match authority)"))
        self._authority_cb.setChecked(False)
        opt.addWidget(self._authority_cb, 2, 0, 1, 2)

        opt.addWidget(QLabel(tr("近似匹配处理")), 2, 2)
        self._near_combo = QComboBox()
        self._near_combo.addItems([tr("强制人工确认"), tr("自动采纳（后续校正）")])
        opt.addWidget(self._near_combo, 2, 3)

        opt.addWidget(QLabel(tr("匹配阶元 (Match upto)")), 3, 0)
        self._rank_combo = QComboBox()
        for label, _rank in _MATCH_UPTO:
            self._rank_combo.addItem(tr(label))
        opt.addWidget(self._rank_combo, 3, 1)

        opt.addWidget(QLabel(tr("限定于高阶类群 (Limit to)")), 3, 2)
        self._limit_edit = QLineEdit()
        self._limit_edit.setPlaceholderText(tr("如 Porifera / Copepoda，避开同名歧义"))
        opt.addWidget(self._limit_edit, 3, 3)
        v.addLayout(opt)

        # 追加列勾选（复刻网站输出列）
        cols_box = QFrame()
        cols_box.setFrameShape(QFrame.Shape.StyledPanel)
        cg = QGridLayout(cols_box)
        cg.addWidget(QLabel(tr("追加 WoRMS 列：")), 0, 0, 1, 4)
        _en_ui = current_language() == "en"
        for idx, (key, zh, en, _fn) in enumerate(MATCH_APPEND_COLUMNS):
            cb = QCheckBox(en if _en_ui else zh)
            cb.setChecked(True)
            self._append_checks[key] = cb
            cg.addWidget(cb, 1 + idx // 4, idx % 4)
        note = QLabel(tr("「完整分类链」勾选时会为每个已接受名额外查一次 WoRMS（有缓存，冷启动稍慢）。"))
        note.setStyleSheet("color:#667085;font-size:11px;")
        note.setWordWrap(True)
        cg.addWidget(note, 1 + (len(MATCH_APPEND_COLUMNS) - 1) // 4 + 1, 0, 1, 4)
        v.addWidget(cols_box)

        # 预览
        self._preview_summary = QLabel("")
        v.addWidget(self._preview_summary)
        self._preview_table = QTableWidget(0, 2)
        self._preview_table.setHorizontalHeaderLabels(["#", tr("学名")])
        self._preview_table.horizontalHeader().setStretchLastSection(True)
        self._preview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        v.addWidget(self._preview_table, 1)

        bar = QHBoxLayout()
        bar.addStretch()
        cancel = QPushButton(tr("取消"))
        cancel.clicked.connect(self.reject)
        self._btn_run = QPushButton(tr("开始匹配 →"))
        self._btn_run.setDefault(True)
        self._btn_run.clicked.connect(self._on_run)
        bar.addWidget(cancel)
        bar.addWidget(self._btn_run)
        v.addLayout(bar)
        return page

    def _build_review_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)

        self._review_summary = QLabel("")
        self._review_summary.setStyleSheet("font-weight:600;")
        v.addWidget(self._review_summary)
        tip = QLabel(tr("双击任意行可在候选中选择 / 标记无匹配。"))
        tip.setStyleSheet("color:#667085;font-size:11px;")
        v.addWidget(tip)

        self._review_table = QTableWidget(0, 8)
        self._review_table.setHorizontalHeaderLabels(
            [tr("状态"), tr("输入名"), tr("匹配名"), tr("接受名"),
             tr("命中类型"), tr("命名人"), "AphiaID", tr("阶元")]
        )
        self._review_table.horizontalHeader().setStretchLastSection(True)
        self._review_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._review_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._review_table.cellDoubleClicked.connect(self._on_review_double_click)
        v.addWidget(self._review_table, 1)

        bar = QHBoxLayout()
        back = QPushButton(tr("← 返回"))
        back.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        bar.addWidget(back)
        bar.addStretch()
        bar.addWidget(QLabel(tr("输出语言")))
        self._lang_combo = QComboBox()
        for label, _code in _OUTPUT_LANG_OPTIONS:
            self._lang_combo.addItem(tr(label))
        bar.addWidget(self._lang_combo)
        self._btn_export = QPushButton(tr("导出标注文件…"))
        self._btn_export.setDefault(True)
        self._btn_export.clicked.connect(self._on_export)
        bar.addWidget(self._btn_export)
        v.addLayout(bar)
        return page

    # ── 逻辑（可单测）───────────────────────────────────────────────────────

    def load_file(self, path: str) -> None:
        self._file_path = path
        self._headers, self._rows = cis.read_table(path, has_header=self._header_cb.isChecked())
        self._file_lbl.setText(path)
        for combo, hints in ((self._name_combo, _NAME_HINTS),
                             (self._author_combo, _AUTHOR_HINTS)):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(_NONE)
            combo.addItems(self._headers)
            guess = self._guess_header(hints)
            if guess:
                combo.setCurrentText(guess)
            combo.blockSignals(False)
        self._refresh_preview()

    def _on_header_toggled(self, _checked: bool) -> None:
        if self._file_path:
            self.load_file(self._file_path)

    def limit_taxon(self) -> Optional[str]:
        txt = self._limit_edit.text().strip()
        return txt or None

    def match_rank(self) -> Optional[str]:
        return _MATCH_UPTO[self._rank_combo.currentIndex()][1]

    def match_authority(self) -> bool:
        return self._authority_cb.isChecked()

    def authorities(self) -> Optional[list[str]]:
        if not self.match_authority():
            return None
        col = self._author_combo.currentText()
        if not col or col == _NONE:
            return None
        return [str(row.get(col, "") or "") for row in self._rows]

    def _guess_header(self, hints: list[str]) -> Optional[str]:
        for h in self._headers:
            hl = h.lower()
            if any(hint.lower() in hl for hint in hints):
                return h
        return None

    def name_column(self) -> Optional[str]:
        txt = self._name_combo.currentText()
        return txt if txt and txt != _NONE else None

    def name_list(self) -> list[str]:
        col = self.name_column()
        if not col:
            return []
        return [str(row.get(col, "") or "") for row in self._rows]

    def selected_append_cols(self) -> list[str]:
        return [k for k, cb in self._append_checks.items() if cb.isChecked()]

    def auto_accept_near(self) -> bool:
        return self._near_combo.currentIndex() == 1

    def set_results(self, results: list[dict]) -> None:
        self._results = results
        self._refresh_review()

    def resolve_row(self, i: int, candidate: Optional[dict], *, mark_none: bool = False) -> None:
        if not (0 <= i < len(self._results)):
            return
        r = self._results[i]
        if mark_none:
            r["best"] = None
            r["resolution"] = "none"
        elif candidate is not None:
            r["best"] = candidate
            r["resolution"] = "matched"
            r.pop("chain", None)  # stale chain from a prior pick
        self._refresh_review()

    def output_lang(self) -> str:
        return _OUTPUT_LANG_OPTIONS[self._lang_combo.currentIndex()][1]

    def export(self, path: str) -> str:
        cols = self.selected_append_cols()
        lang = self.output_lang()
        low = path.lower()
        if low.endswith(".csv"):
            out = export_annotated_csv(self._headers, self._rows, self._results, cols, path, lang=lang)
        else:
            if not low.endswith(".xlsx"):
                path += ".xlsx"
            out = export_annotated_xlsx(self._headers, self._rows, self._results, cols, path, lang=lang)
        return str(out)

    # ── UI 事件 ─────────────────────────────────────────────────────────────

    def _pick_file(self) -> None:
        path = ui.get_open_file_name(
            self, tr("选择学名表"), "",
            tr("表格 (*.xlsx *.xlsm *.csv *.txt);;所有文件 (*)"),
        )
        if path:
            self.load_file(path)

    def _refresh_preview(self) -> None:
        names = self.name_list()
        self._preview_table.setRowCount(0)
        seen: dict[str, int] = {}
        non_blank = 0
        for i, name in enumerate(names):
            n = name.strip()
            row = self._preview_table.rowCount()
            self._preview_table.insertRow(row)
            self._preview_table.setItem(row, 0, QTableWidgetItem(str(i + 1)))
            item = QTableWidgetItem(name)
            if not n:
                item.setForeground(QColor("#b54708"))
            else:
                low = n.lower()
                seen[low] = seen.get(low, 0) + 1
                if seen[low] > 1:
                    item.setForeground(QColor("#b54708"))
                non_blank += 1
            self._preview_table.setItem(row, 1, item)
        uniq = len(seen)
        self._preview_summary.setText(
            tr("待匹配 {} 个名称（去重 {} 个） / 共 {} 行").format(non_blank, uniq, len(names))
        )

    def _on_run(self) -> None:
        if not self.name_column():
            ui.warn(self, tr("批量匹配"), tr("请先选择「学名列」。"))
            return
        names = self.name_list()
        if not any(n.strip() for n in names):
            ui.warn(self, tr("批量匹配"), tr("选中的列没有任何学名。"))
            return
        fetch_chain = "classification" in self.selected_append_cols()
        self._btn_run.setEnabled(False)
        self._progress = QProgressDialog(tr("正在匹配 WoRMS…"), tr("取消"), 0, 0, self)
        self._progress.setWindowTitle(tr("批量匹配"))
        self._progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress.setMinimumDuration(0)
        self._progress.setAutoClose(False)
        self._progress.setAutoReset(False)

        self._worker = WormsMatchWorker(
            self._service, names,
            marine_only=self._marine_cb.isChecked(),
            auto_accept_near=self.auto_accept_near(),
            fetch_chain=fetch_chain,
            limit_taxon=self.limit_taxon(),
            match_authority=self.match_authority(),
            authorities=self.authorities(),
            match_rank=self.match_rank(),
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_match_done)
        self._worker.failed.connect(self._on_match_failed)
        self._progress.canceled.connect(self._worker.requestInterruption)
        self._worker.start()

    def _on_progress(self, done: int, total: int) -> None:
        if self._progress is None:
            return
        if total > 0:
            self._progress.setMaximum(total)
            self._progress.setValue(done)
            self._progress.setLabelText(tr("正在匹配 WoRMS… {}/{}").format(done, total))

    def _on_match_done(self, results: object) -> None:
        if self._progress is not None:
            self._progress.close()
            self._progress = None
        self._btn_run.setEnabled(True)
        self.set_results(list(results))  # type: ignore[arg-type]
        self._stack.setCurrentIndex(1)

    def _on_match_failed(self, msg: str) -> None:
        if self._progress is not None:
            self._progress.close()
            self._progress = None
        self._btn_run.setEnabled(True)
        ui.critical(self, tr("批量匹配失败"), msg)

    def _refresh_review(self) -> None:
        self._review_table.setRowCount(0)
        counts = {"matched": 0, "near": 0, "ambiguous": 0, "none": 0}
        for r in self._results:
            res = r.get("resolution", "none")
            counts[res] = counts.get(res, 0) + 1
            best = r.get("best") or {}
            row = self._review_table.rowCount()
            self._review_table.insertRow(row)
            badge_text, color = _RES_BADGE.get(res, (res, "#667085"))
            cells = [
                tr(badge_text),
                r.get("input", ""),
                str(best.get("scientificname", "")),
                str(best.get("valid_name", "") or best.get("scientificname", "")),
                str(best.get("match_type", "")),
                str(best.get("authority", "")),
                str(best.get("AphiaID", "")),
                str(best.get("rank", "")),
            ]
            for c, val in enumerate(cells):
                item = QTableWidgetItem(val)
                if c == 0:
                    item.setForeground(QColor(color))
                self._review_table.setItem(row, c, item)
        total = len(self._results)
        resolved = counts["matched"]
        pending = counts["near"] + counts["ambiguous"]
        self._review_summary.setText(
            tr("已解析 {} / 待复核 {} / 无匹配 {} / 共 {}").format(
                resolved, pending, counts["none"], total)
        )

    def _on_review_double_click(self, row: int, _col: int) -> None:
        if not (0 <= row < len(self._results)):
            return
        dlg = _CandidatePickerDialog(self._results[row], self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if dlg.mark_none:
                self.resolve_row(row, None, mark_none=True)
            elif dlg.chosen is not None:
                self.resolve_row(row, dlg.chosen)

    def _on_export(self) -> None:
        if not self._results:
            ui.warn(self, tr("导出"), tr("还没有匹配结果。"))
            return
        if not self.selected_append_cols():
            ui.warn(self, tr("导出"), tr("请至少勾选一个要追加的 WoRMS 列。"))
            return
        path = ui.get_save_file_name(
            self, tr("导出匹配结果"), "worms_match.xlsx",
            "Excel (*.xlsx);;CSV (*.csv)",
        )
        if not path:
            return
        try:
            out = self.export(path)
        except Exception as exc:  # noqa: BLE001
            ui.critical(self, tr("导出失败"), str(exc))
            return
        ui.info(self, tr("导出完成"), tr("已导出标注文件：\n{}").format(out))
