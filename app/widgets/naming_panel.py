"""naming_panel.py — 7-segment specimen UID / result-ID generator.

Provides a form with the six/seven naming segments, a live preview of
the generated UID and result-ID, and an optional R-prefix dual-label
warning when the storage code starts with "R".

Uses ``app.utils.naming.build_uid`` and ``build_result_id`` for
real-time assembly.

Signals
-------
uid_generated(uid: str)
    Emitted whenever the preview UID changes (user edits any field).
result_id_generated(result_id: str)
    Emitted whenever the preview result-ID changes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.config import icons
from app.utils.naming import (
    build_result_id,
    build_uid,
    species_sequence_summary,
    specimen_date_seg,
)

if TYPE_CHECKING:
    from app.app_context import AppContext


class NamingPanel(QWidget):
    """7-segment naming generator with live UID/result-ID preview.

    Signals
    -------
    uid_generated(str)
    result_id_generated(str)
    """

    uid_generated = pyqtSignal(str)
    result_id_generated = pyqtSignal(str)
    save_requested = pyqtSignal()

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._setup_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        card = QFrame(self)
        card.setObjectName("PanelCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        from app.config.effects import apply_card_shadow
        apply_card_shadow(card)

        root = QVBoxLayout(card)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # Card header: title + save button
        hdr_row = QHBoxLayout()
        hdr_row.setContentsMargins(0, 0, 0, 0)
        hdr = QLabel("照片编号")
        hdr.setObjectName("CardTitle")
        hdr_row.addWidget(hdr)
        hdr_row.addStretch()
        save_btn = QPushButton("保存")
        save_btn.setObjectName("Outline")
        save_btn.setFixedHeight(28)
        icons.set_button_icon(save_btn, "mdi6.content-save-outline",
                              color=icons.TONE_MUTED, size=14)
        save_btn.setToolTip("把当前输入存到本地，刷新不丢")
        save_btn.clicked.connect(self.save_requested.emit)
        hdr_row.addWidget(save_btn)
        root.addLayout(hdr_row)

        # Form grid: 2-column label-over-field rows.  Each cell is a self-sized
        # QWidget (not a bare nested layout) so the grid reserves correct row
        # heights — bare nested layouts under-report their hint and overlap.
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        def _cell(label: str, field: QWidget) -> QWidget:
            cell = QWidget()
            wrap = QVBoxLayout(cell)
            wrap.setContentsMargins(0, 0, 0, 0)
            wrap.setSpacing(5)
            lbl = QLabel(label)
            lbl.setObjectName("MutedSmall")
            wrap.addWidget(lbl)
            wrap.addWidget(field)
            return cell

        def _field(row: int, col: int, label: str, placeholder: str,
                   *, auto: bool = False) -> QLineEdit:
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            edit.setFixedHeight(32)
            if auto:
                edit.setObjectName("AutoField")
            grid.addWidget(_cell(label, edit), row, col)
            return edit

        # Auto-derived fields shown dashed (web: .auto class)
        self._province = _field(0, 0, "地区", "如 FJ", auto=True)
        self._site = _field(0, 1, "样地", "如 YGLZ", auto=True)
        self._station = _field(1, 0, "站位", "如 B2", auto=True)
        self._species_id = _field(1, 1, "物种拼音缩写编号", "如 DLC001")

        # Current-prefix lookup.  Keep this scoped to the typed prefix instead
        # of rendering a long global list; projects can have hundreds of taxa.
        seq_cell = QWidget()
        seq_lay = QHBoxLayout(seq_cell)
        seq_lay.setContentsMargins(0, 0, 0, 0)
        seq_lay.setSpacing(8)
        self._seq_hint_label = QLabel("输入物种缩写后显示下一个编号")
        self._seq_hint_label.setObjectName("MutedSmall")
        self._seq_hint_label.setWordWrap(True)
        seq_lay.addWidget(self._seq_hint_label, stretch=1)
        self._seq_apply_btn = QPushButton("填入建议")
        self._seq_apply_btn.setObjectName("Ghost")
        self._seq_apply_btn.setFixedHeight(28)
        self._seq_apply_btn.setEnabled(False)
        self._seq_apply_btn.clicked.connect(self._apply_sequence_suggestion)
        seq_lay.addWidget(self._seq_apply_btn)
        grid.addWidget(seq_cell, 2, 0, 1, 2)

        self._collection_date = _field(3, 0, "采集日期", "YYYYMMDD")
        self._photo_date = _field(3, 1, "拍摄日期", "YYYYMMDD（选填）")

        # ── 保存方式 button group (row 4, full width) ──
        # Hidden QLineEdit proxy for backward-compat with workbench_view and tests
        self._storage = QLineEdit()
        self._storage.setPlaceholderText("如 T95E / RD75E")
        self._storage.setFixedHeight(32)
        self._storage.textChanged.connect(self._update_preview)

        storage_cell = QWidget()
        sc_lay = QVBoxLayout(storage_cell)
        sc_lay.setContentsMargins(0, 0, 0, 0)
        sc_lay.setSpacing(5)
        sc_lbl = QLabel("保存方式")
        sc_lbl.setObjectName("MutedSmall")
        sc_lay.addWidget(sc_lbl)

        # Button group row
        btn_row_storage = QHBoxLayout()
        btn_row_storage.setContentsMargins(0, 0, 0, 0)
        btn_row_storage.setSpacing(4)
        self._storage_btn_group = QButtonGroup(self)
        self._storage_btn_group.setExclusive(True)
        for code in ("T95E", "D95E", "D75E", "T75E", "D79", "T79", "T100"):
            b = QPushButton(code)
            b.setCheckable(True)
            b.setObjectName("StorageBtn")
            b.setFixedHeight(26)
            b.clicked.connect(lambda _=False, c=code: self._on_storage_btn(c))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self._storage_btn_group.addButton(b)
            btn_row_storage.addWidget(b)
        btn_row_storage.addStretch()
        sc_lay.addLayout(btn_row_storage)
        # Custom/free-text field below the buttons
        sc_lay.addWidget(self._storage)
        grid.addWidget(storage_cell, 4, 0, 1, 2)  # full row span

        # Sequence number (row 5)
        # auto-虚线: show as dashed QLabel preview, SpinBox for actual input
        self._seq = QSpinBox()
        self._seq.setMinimum(1)
        self._seq.setMaximum(999)
        self._seq.setValue(1)
        self._seq.setFixedHeight(32)
        grid.addWidget(_cell("成果序号（auto）", self._seq), 5, 0)

        root.addLayout(grid)

        # ── Live preview blocks ──
        root.addSpacing(4)
        preview_lbl = QLabel("标本唯一编号")
        preview_lbl.setObjectName("MutedSmall")
        root.addWidget(preview_lbl)
        self._uid_preview = QLabel("—")
        self._uid_preview.setObjectName("PreviewEmpty")
        self._uid_preview.setWordWrap(True)
        self._uid_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self._uid_preview)

        result_lbl = QLabel("成果编号（含序号）")
        result_lbl.setObjectName("MutedSmall")
        root.addWidget(result_lbl)
        self._result_preview = QLabel("—")
        self._result_preview.setObjectName("PreviewEmpty")
        self._result_preview.setWordWrap(True)
        self._result_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self._result_preview)

        # R-prefix dual-label warning (hidden by default)
        self._rna_warning = QLabel(
            "R 前缀（已取 RNA）— 需额外生成 RNAlater 组织管标签"
        )
        self._rna_warning.setObjectName("RnaWarning")
        self._rna_warning.setWordWrap(True)
        self._rna_warning.hide()
        root.addWidget(self._rna_warning)

        # Duplicate-UID warning (naming-dup-warn, hidden by default)  #cursor
        self._dup_warn = QLabel("⚠ 编号重复 — 该编号已存在")
        self._dup_warn.setObjectName("UnattributedWarning")
        self._dup_warn.setWordWrap(True)
        self._dup_warn.hide()
        root.addWidget(self._dup_warn)

        # Design-compliance warning (7-segment format issues)  #cursor
        self._compliance_warn = QLabel("")
        self._compliance_warn.setObjectName("UnattributedWarning")
        self._compliance_warn.setWordWrap(True)
        self._compliance_warn.hide()
        root.addWidget(self._compliance_warn)

        # naming-action-row: 📌 添加到侧栏 + copy shortcuts
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 4, 0, 0)
        action_row.setSpacing(8)

        self._pin_btn = QPushButton("📌 添加到侧栏")
        self._pin_btn.setObjectName("Outline")
        self._pin_btn.setFixedHeight(32)
        self._pin_btn.setToolTip("把当前编号保存并添加到左侧标本列表")
        self._pin_btn.clicked.connect(self.save_requested.emit)
        action_row.addWidget(self._pin_btn)

        copy_uid_btn = QPushButton("复制 UID")
        copy_uid_btn.setObjectName("Ghost")
        copy_uid_btn.setFixedHeight(32)
        icons.set_button_icon(copy_uid_btn, "mdi6.content-copy", color=icons.TONE_MUTED, size=14)
        copy_uid_btn.clicked.connect(self._copy_uid)
        action_row.addWidget(copy_uid_btn)

        copy_rid_btn = QPushButton("复制成果编号")
        copy_rid_btn.setObjectName("Ghost")
        copy_rid_btn.setFixedHeight(32)
        icons.set_button_icon(copy_rid_btn, "mdi6.content-copy", color=icons.TONE_MUTED, size=14)
        copy_rid_btn.clicked.connect(self._copy_result_id)
        action_row.addWidget(copy_rid_btn)
        root.addLayout(action_row)

        root.addStretch()

        # Wire all edits to live-preview
        for widget in (
            self._province, self._site, self._station,
            self._species_id, self._storage,
            self._collection_date, self._photo_date,
        ):
            widget.textChanged.connect(self._update_preview)
        self._seq.valueChanged.connect(self._update_preview)

    # ── Public API ────────────────────────────────────────────────────────────

    def load_specimen(self, sp: dict) -> None:
        """Populate fields from a specimen dict (uses sp field names from Specimen model).

        Chinese fields (taxonGroupCn, orderCn …) are NOT auto-filled — user
        fills those manually per the project constraint.
        """
        self._province.setText(sp.get("province") or "")
        self._site.setText(sp.get("site") or "")
        self._station.setText(sp.get("station") or "")
        self._species_id.setText(sp.get("id") or "")
        storage_val = sp.get("storage") or ""
        self._storage.setText(storage_val)
        # Sync storage button group with loaded value
        for btn in self._storage_btn_group.buttons():
            btn.setChecked(btn.text() == storage_val)
        self._collection_date.setText(sp.get("collectionDate") or sp.get("collection_date") or "")
        self._photo_date.setText(sp.get("photoDate") or sp.get("photo_date") or "")
        # seq hint
        hint = sp.get("nextResultSequenceHint") or sp.get("next_result_sequence_hint") or 1
        try:
            self._seq.setValue(int(hint))
        except (ValueError, TypeError):
            self._seq.setValue(1)
        self._update_preview()

    def current_uid(self) -> str:
        return self._uid_preview.text() if self._uid_preview.text() != "—" else ""

    def current_result_id(self) -> str:
        return self._result_preview.text() if self._result_preview.text() != "—" else ""

    def show_dup_warn(self, show: bool = True) -> None:
        """Show or hide the duplicate-UID warning label."""
        if show:
            self._dup_warn.show()
        else:
            self._dup_warn.hide()

    def current_sequence_suggestion(self) -> str:
        """Return the currently suggested species id, e.g. ``DLC004``."""
        return str(self._seq_apply_btn.property("suggested_id") or "")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _update_preview(self) -> None:
        province = self._province.text().strip()
        site = self._site.text().strip()
        station = self._station.text().strip()
        species_id = self._species_id.text().strip()
        storage = self._storage.text().strip()
        col_date = self._collection_date.text().strip()
        photo_date = self._photo_date.text().strip()
        seq = self._seq.value()

        date_seg = specimen_date_seg(col_date or None, photo_date or None)

        uid = build_uid(
            province=province or None,
            site=site or None,
            station=station or None,
            species_id=species_id or None,
            storage=storage or None,
            date_seg=date_seg or None,
        )
        result_id = build_result_id(
            province=province or None,
            site=site or None,
            station=station or None,
            species_id=species_id or None,
            storage=storage or None,
            date_seg=date_seg or None,
            seq=seq,
        )

        self._set_preview(self._uid_preview, uid)
        self._set_preview(self._result_preview, result_id)

        # R-prefix dual-label warning
        if storage.upper().startswith("R"):
            self._rna_warning.show()
        else:
            self._rna_warning.hide()

        if uid:
            self.uid_generated.emit(uid)
        if result_id:
            self.result_id_generated.emit(result_id)

        self._update_sequence_hint(species_id)
        self._check_duplicate(uid)    # #cursor findDuplicateSpecimen
        self._check_compliance(uid)   # #cursor designComplianceCheck

    def _update_sequence_hint(self, species_text: str) -> None:
        """Refresh the per-prefix next-number hint from the project DB."""
        suggested = ""
        if not species_text.strip():
            self._seq_hint_label.setText("输入物种缩写后显示下一个编号")
            self._seq_apply_btn.setEnabled(False)
            self._seq_apply_btn.setText("填入建议")
            self._seq_apply_btn.setProperty("suggested_id", "")
            return

        db = None
        try:
            db = self.ctx.get_db()
        except Exception:
            db = None
        if not db:
            self._seq_hint_label.setText("打开项目后可检查当前前缀的下一个编号")
            self._seq_apply_btn.setEnabled(False)
            self._seq_apply_btn.setText("填入建议")
            self._seq_apply_btn.setProperty("suggested_id", "")
            return

        try:
            summary = species_sequence_summary(
                db,
                species_text,
                project_dir=getattr(self.ctx, "current_project_dir", None),
            )
        except Exception:
            self._seq_hint_label.setText("编号检查暂不可用")
            self._seq_apply_btn.setEnabled(False)
            self._seq_apply_btn.setText("填入建议")
            self._seq_apply_btn.setProperty("suggested_id", "")
            return

        if not summary.prefix or not summary.next_id:
            self._seq_hint_label.setText("请输入字母前缀，如 DLC")
            self._seq_apply_btn.setEnabled(False)
            self._seq_apply_btn.setText("填入建议")
            self._seq_apply_btn.setProperty("suggested_id", "")
            return

        suggested = summary.next_id
        if summary.max_number:
            max_id = f"{summary.prefix}{summary.max_number:0{summary.width}d}"
            text = (
                f"{summary.prefix} 已用 {len(summary.used_numbers)} 个，"
                f"最大 {max_id}，建议 {summary.next_id}"
            )
            if summary.gaps:
                shown = ", ".join(f"{n:0{summary.width}d}" for n in summary.gaps[:5])
                more = "..." if len(summary.gaps) > 5 else ""
                text += f"；缺号 {shown}{more}"
        else:
            text = f"{summary.prefix} 尚未使用，建议 {summary.next_id}"
        self._seq_hint_label.setText(text)
        self._seq_apply_btn.setText(f"填入 {suggested}")
        self._seq_apply_btn.setProperty("suggested_id", suggested)
        self._seq_apply_btn.setEnabled(True)

    def _check_duplicate(self, uid: str) -> None:
        """Check if *uid* already exists in the DB.

        Mirrors web findDuplicateSpecimen() app.js:3853.
        Shows/hides self._dup_warn accordingly.
        """
        if not uid:
            self._dup_warn.hide()
            return
        db = None
        try:
            db = self.ctx.get_db()
        except Exception:
            db = None
        if not db:
            self._dup_warn.hide()
            return
        # Check if this uid already has a specimens row
        try:
            row = db.execute(
                "SELECT owner_project_dir FROM specimens WHERE uid = ?", (uid,)
            ).fetchone()
        except Exception:
            self._dup_warn.hide()
            return
        if row:
            owner = row[0] if row[0] else "未知项目"
            from pathlib import Path as _Path
            project_name = _Path(owner).name if owner else "标本库"
            self._dup_warn.setText(
                f"⚠ 编号重复 — 该唯一编号已存在（项目「{project_name}」），请修改字段后再保存"
            )
            self._dup_warn.show()
        else:
            self._dup_warn.hide()

    def _check_compliance(self, uid: str) -> None:
        """Light 7-segment format compliance check for the live preview UID.

        Mirrors web designComplianceCheck() app.js:1954.
        Shows self._compliance_warn if the UID is partially filled but
        has obvious format issues (empty required segments).
        """
        province = self._province.text().strip()
        site = self._site.text().strip()
        station = self._station.text().strip()
        species_id = self._species_id.text().strip()
        storage = self._storage.text().strip()
        col_date = self._collection_date.text().strip()

        issues: list[str] = []

        # Only check when the user has started filling in fields
        any_filled = any([province, site, station, species_id, storage, col_date])
        if not any_filled:
            self._compliance_warn.hide()
            return

        if province and not province.isalpha():
            issues.append("地区应为字母（如 FJ）")
        if site and len(site) < 2:
            issues.append("样地代码太短")
        if col_date and len(col_date) != 8:
            issues.append("采集日期应为 8 位 YYYYMMDD")
        if storage and not any(storage.upper().startswith(c) for c in ("T", "D", "R")):
            issues.append("保存方式应以 T/D/R 开头")

        if issues:
            self._compliance_warn.setText("⚠ 格式提示：" + "；".join(issues))
            self._compliance_warn.show()
        else:
            self._compliance_warn.hide()

    def _apply_sequence_suggestion(self) -> None:
        suggested = self.current_sequence_suggestion()
        if suggested:
            self._species_id.setText(suggested)
            self._species_id.setFocus()

    def _set_preview(self, label: QLabel, value: str) -> None:
        """Set preview text and swap filled/empty styling via object name."""
        filled = bool(value)
        label.setText(value if filled else "—")
        label.setObjectName("PreviewBlock" if filled else "PreviewEmpty")
        label.style().unpolish(label)
        label.style().polish(label)

    def _copy_uid(self) -> None:
        from PyQt6.QtWidgets import QApplication
        uid = self.current_uid()
        if uid:
            QApplication.clipboard().setText(uid)

    def _copy_result_id(self) -> None:
        from PyQt6.QtWidgets import QApplication
        rid = self.current_result_id()
        if rid:
            QApplication.clipboard().setText(rid)

    def _on_storage_btn(self, code: str) -> None:
        """Called when a storage button group button is clicked.

        Sets the free-text storage QLineEdit to the selected code and
        un-checks all other buttons.  The QLineEdit.textChanged signal
        will trigger _update_preview automatically.
        """
        self._storage.setText(code)
        # Sync button checked state: check the matching button, uncheck others
        for btn in self._storage_btn_group.buttons():
            btn.setChecked(btn.text() == code)
