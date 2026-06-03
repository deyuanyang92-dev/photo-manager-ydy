"""retroactive_modal.py — Retroactive organize dialog.

Shows scan results (specimens + groups with JPG counts), lets user select/deselect
groups, and confirm to archive.  Mirrors renderRetroactiveModal() app.js:8113–8198.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from app.app_context import AppContext


class RetroactiveModal(QDialog):
    """Retroactive organize: show scan result, confirm → archive groups.

    Oracle: renderRetroactiveModal() app.js:8113–8198.
    """

    def __init__(self, ctx: "AppContext", scan_result: dict,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._scan = scan_result
        self._sel: dict[str, bool] = {}  # uid#seq → selected
        self._delete_jpg = False
        self.setWindowTitle("存量整理 — 按时间配对 JPG → TIF")
        self.setMinimumSize(640, 480)
        self._setup_ui()
        self._populate()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        hint = QLabel(
            "扫描 results/ 的 TIF + incoming-jpg/ 原片，"
            "按拍摄时间把每个 TIF 之前的 JPG 配给它。"
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._content = QWidget()
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(0, 0, 0, 0)
        self._content_lay.setSpacing(8)
        self._content_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._content)
        root.addWidget(scroll, stretch=1)

        # Footer: delete-jpg toggle + buttons
        foot = QHBoxLayout()
        self._del_cb = QCheckBox("打包后删除原 JPG（校验通过才删，TIFF 永久保留）")
        self._del_cb.setChecked(False)
        self._del_cb.toggled.connect(lambda v: setattr(self, "_delete_jpg", v))
        foot.addWidget(self._del_cb)
        foot.addStretch()

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("确认整理")
        btns.accepted.connect(self._on_apply)
        btns.rejected.connect(self.reject)
        foot.addWidget(btns)
        root.addLayout(foot)

    def _populate(self) -> None:
        specimens = self._scan.get("specimens", [])
        # Default: check all groups with JPGs
        for sp in specimens:
            for g in sp.get("groups", []):
                key = f"{sp['uid']}#{g['seq']}"
                self._sel[key] = g["jpgCount"] > 0

        for sp in specimens:
            card = QFrame()
            card.setObjectName("Panel")
            card_lay = QVBoxLayout(card)
            card_lay.setContentsMargins(12, 10, 12, 10)
            card_lay.setSpacing(6)
            uid_lbl = QLabel(sp["uid"])
            uid_lbl.setObjectName("Mono")
            card_lay.addWidget(uid_lbl)
            for g in sp.get("groups", []):
                key = f"{sp['uid']}#{g['seq']}"
                row = QHBoxLayout()
                cb = QCheckBox()
                cb.setChecked(bool(self._sel.get(key, False)))
                cb.setEnabled(g["jpgCount"] > 0)
                row.addWidget(cb)
                if g["jpgCount"] > 0:
                    txt = (
                        f"成果 #{g['seq']}  {g['tiffName']}  ← "
                        f"{g['jpgCount']} 张原片"
                    )
                else:
                    txt = (
                        f"成果 #{g['seq']}  {g['tiffName']}  ← "
                        "⚠ 没配到原片（不可打包）"
                    )
                lbl = QLabel(txt)
                lbl.setObjectName("MutedSmall" if g["jpgCount"] == 0 else "")
                row.addWidget(lbl, stretch=1)
                cb.toggled.connect(lambda v, k=key: self._sel.update({k: v}))
                card_lay.addLayout(row)
                if g["jpgCount"] > 0:
                    names = ", ".join(Path(p).name for p in g["jpgPaths"][:5])
                    if len(g["jpgPaths"]) > 5:
                        names += f"…（共 {len(g['jpgPaths'])} 张）"
                    sub = QLabel(names)
                    sub.setObjectName("MutedSmall")
                    sub.setIndent(24)
                    card_lay.addWidget(sub)
            self._content_lay.addWidget(card)

        # Unassigned JPGs warning
        ua = self._scan.get("unassignedJpgs", [])
        if ua:
            warn = QLabel(f"⚠ {len(ua)} 张 JPG 没配到任何 TIF（不打包、不删除）")
            warn.setObjectName("MutedSmall")
            self._content_lay.addWidget(warn)

        if not specimens:
            empty = QLabel("没找到可整理的成片。")
            empty.setObjectName("Muted")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._content_lay.addWidget(empty)

    def _on_apply(self) -> None:
        from app.services.archive_service import archive_group
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            QMessageBox.warning(self, "整理", "未设置项目目录。")
            return

        # Collect confirmed groups
        specimens = self._scan.get("specimens", [])
        to_archive = []
        for sp in specimens:
            for g in sp.get("groups", []):
                key = f"{sp['uid']}#{g['seq']}"
                if self._sel.get(key) and g["jpgCount"] > 0:
                    to_archive.append((sp["uid"], g))

        if not to_archive:
            QMessageBox.information(self, "整理", "请至少勾选一个有原片的组。")
            return

        confirm = QMessageBox.question(
            self, "确认整理",
            f"对 {len(to_archive)} 组打包归档（JXL+ZIP）？"
            + ("\n⚠ 已开启删原片：打包校验通过后将删除这些 JPG（TIFF 永久保留）。"
               if self._delete_jpg else ""),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        from app.services.retroactive_service import FileResult
        from app.views.workbench_view import _BatchResultDialog
        file_results: list[FileResult] = []
        for uid, g in to_archive:
            tiff_name = Path(g["tiffPath"]).name
            try:
                ar = archive_group(
                    jpg_paths=g["jpgPaths"],
                    tiff_path=g["tiffPath"],
                    project_dir=project_dir,
                    delete_jpg=self._delete_jpg,
                )
                zip_size = 0
                if ar.zip_path:
                    try:
                        import os as _os
                        zip_size = _os.path.getsize(ar.zip_path)
                    except OSError:
                        pass
                file_results.append(FileResult(
                    name=tiff_name, ok=True, size_bytes=zip_size, error=""
                ))
            except Exception as exc:
                file_results.append(FileResult(
                    name=tiff_name, ok=False, size_bytes=0, error=str(exc)
                ))

        dlg = _BatchResultDialog(file_results, parent=self)
        dlg.exec()
        self.accept()
