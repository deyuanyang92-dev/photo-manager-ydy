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
        self._delete_jpg = True
        self.setWindowTitle(
            "执行整理归档" if scan_result.get("autoGroup")
            else "存量整理 — 按时间配对 JPG → TIF"
        )
        self.setMinimumSize(640, 480)
        self._setup_ui()
        self._populate()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        if self._scan.get("autoGroup"):
            hint_text = (
                "以下为已预览的自动分组结果。"
                "勾选要打包的组，点「确认整理」才会开始归档。"
            )
        else:
            hint_text = (
                "扫描 results/ 的 TIF + incoming-jpg/ 原片，"
                "按拍摄时间把每个 TIF 之前的 JPG 配给它。"
            )
        if not self.ctx.current_project_dir:
            hint_text += (
                "\n\n未打开项目：仍可打包归档（ZIP 写在 TIF 同目录）；"
                "不会写入项目数据库。"
            )
        hint = QLabel(hint_text)
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
        self._del_cb = QCheckBox("打包后删除原 JPG（校验通过才删，不自动删 TIFF）")
        self._del_cb.setChecked(True)
        self._del_cb.setChecked(False)
        self._del_cb.toggled.connect(lambda v: setattr(self, "_delete_jpg", v))
        foot.addWidget(self._del_cb)
        foot.addStretch()

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("确认整理")
        btns.accepted.connect(self._archive_selected_existing_groups)
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
                    if g.get("tiffNameValid") is False:
                        txt += "  ⚠ TIF 命名不符合规则"
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

        unnamed = self._scan.get("unnamedTiffs", [])
        if unnamed:
            warn = QLabel(
                f"⚠ {len(unnamed)} 个 TIF 无法识别标本编号，已跳过；"
                "请先规范命名或在对应标本分组中重新扫描。"
            )
            warn.setObjectName("MutedSmall")
            warn.setWordWrap(True)
            self._content_lay.addWidget(warn)

        if not specimens:
            empty = QLabel("没找到可整理的成片。")
            empty.setObjectName("Muted")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._content_lay.addWidget(empty)

    def _archive_selected_existing_groups(self) -> None:
        from app.services.archive_service import archive_group
        project_dir = self.ctx.current_project_dir or self._scan.get("scanFolder") or ""
        if not project_dir:
            QMessageBox.warning(self, "整理", "未选择扫描目录。")
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

        no_project = self.ctx.get_db() is None
        confirm = QMessageBox.question(
            self, "确认整理",
            f"对 {len(to_archive)} 组打包归档（JPG ZIP）？"
            + ("\n\n未打开项目：仅生成本地 ZIP，不写入项目数据库。"
               if no_project else "")
            + ("\n⚠ 已开启删原片：打包校验通过后将删除这些 JPG；TIFF 不会被自动删除。"
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
                if self._scan.get("autoGroup"):
                    db = self.ctx.get_db()
                    if db is not None:
                        from app.services.retroactive_service import register_auto_group
                        register_auto_group(
                            db,
                            uid,
                            g,
                            archive_zip=ar.zip_path,
                        )
            except Exception as exc:
                file_results.append(FileResult(
                    name=tiff_name, ok=False, size_bytes=0, error=str(exc)
                ))

        dlg = _BatchResultDialog(file_results, parent=self)
        dlg.exec()
        self.accept()
