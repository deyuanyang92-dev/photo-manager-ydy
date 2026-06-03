# Workbench Full Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire every workbench feature gap identified in `docs/specs/workbench-functional.md` so the PyQt6 workbench matches the web prototype's full behavior.

**Architecture:** The existing service layer (`activation_service`, `monitor_service`, `grouping_service`, `organize_service`, `archive_service`, `helicon_service`) is already correct. This plan wires the UI gaps: auto-poll timer, add-to-group from monitor, JPG deletion, Helicon params, sequence naming on compose, free-compose, retroactive-scan modal, project-settings drawer, and supporting test coverage.

**Tech Stack:** Python 3.13, PyQt6, SQLite (via `app/db/db_manager.py`), pytest with `QT_QPA_PLATFORM=offscreen`.

**Files touched:**
- Modify: `app/views/workbench_view.py`
- Modify: `app/widgets/monitor_panel.py`
- Modify: `app/widgets/grouping_panel.py`
- Modify: `app/widgets/results_column.py`
- Create: `app/widgets/helicon_params_panel.py`
- Create: `app/widgets/retroactive_modal.py`
- Create: `app/widgets/project_settings_drawer.py`
- Modify: `tests/test_workbench_view.py`
- Create: `tests/test_workbench_wiring.py`

**Constraint reminder (hard rules that must never be violated):**
- TIFF永远保留 — `archive_group` only deletes JPG, never TIFF.
- `delete_jpg` defaults to `False`.
- Chinese fields (`taxon_group_cn`, `order_cn`, etc.) are NEVER auto-filled.
- Activation is mutually exclusive: at most one specimen active at a time.
- Single "删除" button with TIFF warning — not multiple delete paths.

---

### Task 1: Auto-poll timer in WorkbenchView

**Files:**
- Modify: `app/views/workbench_view.py`
- Modify: `tests/test_workbench_view.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_workbench_view.py — add to TestOnActivate class:
def test_auto_poll_timer_starts_on_activate(self, tmp_path):
    """on_activate must start _auto_refresh_timer."""
    from app.views.workbench_view import WorkbenchView
    project_dir = str(tmp_path / "proj")
    Path(project_dir).mkdir(parents=True)
    (Path(project_dir) / "incoming-jpg").mkdir()
    (Path(project_dir) / "results").mkdir()
    (Path(project_dir) / "_data").mkdir()
    db_path = str(tmp_path / "proj" / "_data" / "project.db")
    db = _make_db(db_path)
    ctx = _make_ctx(project_dir=project_dir, db=db)
    w = WorkbenchView(ctx)
    w.on_activate()
    assert hasattr(w, "_auto_refresh_timer")
    assert w._auto_refresh_timer.isActive()
    db.close()

def test_auto_poll_timer_stops_on_deactivate(self, tmp_path):
    """on_deactivate (BaseView) must stop _auto_refresh_timer."""
    from app.views.workbench_view import WorkbenchView
    project_dir = str(tmp_path / "proj2")
    Path(project_dir).mkdir(parents=True)
    (Path(project_dir) / "incoming-jpg").mkdir()
    (Path(project_dir) / "results").mkdir()
    (Path(project_dir) / "_data").mkdir()
    db_path = str(tmp_path / "proj2" / "_data" / "project.db")
    db = _make_db(db_path)
    ctx = _make_ctx(project_dir=project_dir, db=db)
    w = WorkbenchView(ctx)
    w.on_activate()
    w.on_deactivate()
    assert not w._auto_refresh_timer.isActive()
    db.close()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && python -m pytest tests/test_workbench_view.py::TestOnActivate::test_auto_poll_timer_starts_on_activate -xvs 2>&1 | tail -20
```
Expected: FAIL (AttributeError or assert fails — timer not active)

- [ ] **Step 3: Add `_auto_refresh_timer` to WorkbenchView**

In `app/views/workbench_view.py`, inside `_setup_ui()` after `self._save_timer`:
```python
        # Auto-refresh monitor directory every 2 s (mirrors web startMonitorPoll)
        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.setInterval(2000)
        self._auto_refresh_timer.timeout.connect(self._refresh_monitor)
```

In `on_activate()`, after `self._refresh_monitor()`:
```python
        # Start auto-poll (mirrors web startMonitorPoll)
        if not self._auto_refresh_timer.isActive():
            self._auto_refresh_timer.start()
```

Add `on_deactivate()` method to WorkbenchView (BaseView hook):
```python
    def on_deactivate(self) -> None:
        """Called when navigating away; stop auto-poll."""
        self._auto_refresh_timer.stop()
```

- [ ] **Step 4: Run tests**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && python -m pytest tests/test_workbench_view.py -x --tb=short -q 2>&1 | tail -10
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && git add app/views/workbench_view.py tests/test_workbench_view.py && git commit -m "feat(workbench): auto-poll timer 2s — mirrors web startMonitorPoll"
```

---

### Task 2: Actual JPG deletion in MonitorPanel

**Files:**
- Modify: `app/widgets/monitor_panel.py`
- Modify: `tests/test_workbench_view.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_workbench_view.py — add to TestDeleteWithTiffWarning:
def test_actual_jpg_deletion(self, tmp_path):
    """_on_delete_clicked must actually call os.unlink on confirmed JPG paths."""
    from app.widgets.monitor_panel import MonitorPanel
    from app.services.monitor_service import FileEntry, ScanResult
    ctx = _make_ctx()
    # Create a real temporary JPG file
    jpg_path = str(tmp_path / "test.jpg")
    with open(jpg_path, "wb") as f:
        f.write(b"JFIF" * 100)
    w = MonitorPanel(ctx)
    entries = [FileEntry(
        name="test.jpg", path=jpg_path, kind="jpg",
        size=400, mtime="2026-06-01T00:00:00+00:00",
    )]
    result = ScanResult(project_dir=str(tmp_path), jpg_files=entries)
    w.load_scan(result)
    w._on_select_all()
    # Patch QMessageBox.question to return Yes automatically
    from unittest.mock import patch
    from PyQt6.QtWidgets import QMessageBox
    with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
        w._on_delete_clicked()
    assert not os.path.exists(jpg_path), "JPG must be deleted after confirm"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && python -m pytest "tests/test_workbench_view.py::TestDeleteWithTiffWarning::test_actual_jpg_deletion" -xvs 2>&1 | tail -20
```
Expected: FAIL — file still exists

- [ ] **Step 3: Implement deletion in MonitorPanel._on_delete_clicked**

In `app/widgets/monitor_panel.py`, locate `_on_delete_clicked()`. It currently shows a warning and does nothing for actual deletion. Add after the confirm dialog:

```python
    def _on_delete_clicked(self) -> None:
        selected = self._selected_cards()
        if not selected:
            return
        paths = [getattr(c._entry, "path", "") for c in selected]
        jpg_paths = [p for p in paths if p and not p.lower().endswith((".tif", ".tiff"))]
        tiff_paths = [p for p in paths if p.lower().endswith((".tif", ".tiff"))]

        if tiff_paths:
            QMessageBox.warning(
                self, "无法删除 TIFF",
                f"选中包含 {len(tiff_paths)} 个 TIFF 成片。\n"
                "TIFF 永远保留，只有 JPG 原片可以删除。\n"
                "请取消选择 TIFF 后再操作。"
            )
            return

        if not jpg_paths:
            QMessageBox.information(self, "删除", "请先选中要删除的 JPG。")
            return

        reply = QMessageBox.question(
            self, "确认删除",
            f"确认删除 {len(jpg_paths)} 张 JPG？\n"
            "此操作直接从磁盘删除原片，不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        import os as _os
        errors = []
        for p in jpg_paths:
            try:
                if _os.path.isfile(p):
                    _os.unlink(p)
            except OSError as e:
                errors.append(f"{_os.path.basename(p)}: {e}")

        if errors:
            QMessageBox.warning(self, "删除部分失败", "\n".join(errors))

        self._on_select_none()
        self.refresh_requested.emit()
```

- [ ] **Step 4: Run tests**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && python -m pytest tests/test_workbench_view.py -x --tb=short -q 2>&1 | tail -10
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && git add app/widgets/monitor_panel.py tests/test_workbench_view.py && git commit -m "feat(monitor): wire actual os.unlink for JPG delete with TIFF guard"
```

---

### Task 3: Add-to-group from monitor selection

Adds a "加入分组" button/action flow: selected JPGs in MonitorPanel can be pushed into a chosen group in GroupingPanel via the workbench view.

**Files:**
- Modify: `app/widgets/monitor_panel.py`
- Modify: `app/widgets/grouping_panel.py`
- Modify: `app/views/workbench_view.py`
- Modify: `tests/test_workbench_view.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_workbench_view.py — new class:
class TestAddToGroup:
    def test_monitor_panel_has_add_to_group_signal(self):
        """MonitorPanel must emit add_to_group_requested(group_index: int, paths: list)."""
        from app.widgets.monitor_panel import MonitorPanel
        ctx = _make_ctx()
        w = MonitorPanel(ctx)
        assert hasattr(w, "add_to_group_requested")

    def test_grouping_panel_add_jpgs_to_group(self):
        """GroupingPanel.add_jpgs_to_group(group_index, paths) must add paths to the group."""
        from app.widgets.grouping_panel import GroupingPanel
        from app.services.grouping_service import Group, SpecimenGrouping
        ctx = _make_ctx()
        w = GroupingPanel(ctx)
        sg = SpecimenGrouping(
            uid="FJ-XM-B2-DLC001-T95E-20260601",
            groups=[Group(group_index=0, angle_label="正面", jpg_paths=[])],
        )
        w.load_grouping("FJ-XM-B2-DLC001-T95E-20260601", sg)
        w.add_jpgs_to_group(0, ["/fake/a.jpg", "/fake/b.jpg"])
        assert "/fake/a.jpg" in w._grouping.groups[0].jpg_paths
        assert "/fake/b.jpg" in w._grouping.groups[0].jpg_paths
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && python -m pytest "tests/test_workbench_view.py::TestAddToGroup" -xvs 2>&1 | tail -20
```
Expected: FAIL (signal/method missing)

- [ ] **Step 3: Add signal to MonitorPanel**

In `app/widgets/monitor_panel.py`, add to the class-level signal declarations:
```python
    add_to_group_requested = pyqtSignal(int, list)  # group_index, jpg_paths
```

Add a method `selected_jpg_paths()` to MonitorPanel:
```python
    def selected_jpg_paths(self) -> list[str]:
        """Return absolute paths of all currently selected JPG cards."""
        return [
            c._entry.path
            for c in self._selected_cards()
            if getattr(c._entry, "kind", "") == "jpg" and c._entry.path
        ]
```

- [ ] **Step 4: Add `add_jpgs_to_group` to GroupingPanel**

In `app/widgets/grouping_panel.py`, add:
```python
    def add_jpgs_to_group(self, group_index: int, jpg_paths: list[str]) -> None:
        """Add *jpg_paths* to the group at *group_index* (mutual-exclusion: remove from other groups first).

        Mirrors web groupingAddSelectedToGroup() app.js:5258–5271.
        """
        if not self._grouping:
            return
        # P1: remove paths from all other groups (mutual exclusion)
        for g in self._grouping.groups:
            if g.group_index != group_index:
                g.jpg_paths = [p for p in g.jpg_paths if p not in jpg_paths]
        # P2: add to target group (no duplicates)
        target = next((g for g in self._grouping.groups if g.group_index == group_index), None)
        if target is None:
            return
        for p in jpg_paths:
            if p not in target.jpg_paths:
                target.jpg_paths.append(p)
        self._rebuild()
        self.grouping_changed.emit()
```

- [ ] **Step 5: Wire in WorkbenchView**

In `app/views/workbench_view.py`, connect monitor's add_to_group_requested signal.
After `self._monitor.assign_requested.connect(self._on_assign_jpg)`:
```python
        self._monitor.add_to_group_requested.connect(self._on_add_to_group)
```

Add handler:
```python
    def _on_add_to_group(self, group_index: int, jpg_paths: list[str]) -> None:
        """Add selected monitor JPGs to the specified grouping group."""
        self._grouping.add_jpgs_to_group(group_index, jpg_paths)
        # Also mark those paths as manually assigned to the current uid
        uid = self._current_uid
        project_dir = self.ctx.current_project_dir
        if uid and project_dir and jpg_paths:
            try:
                from app.services.activation_service import manual_assign
                manual_assign(project_dir, uid, jpg_paths)
            except Exception:
                pass
```

- [ ] **Step 6: Add "加入分组" button to GroupingPanel per-group drag hint**

In `app/widgets/grouping_panel.py` `_DraftGroupRow._setup_ui()`, add after the `header` layout:
```python
        add_sel_btn = QPushButton("← 加入所选 JPG")
        add_sel_btn.setObjectName("Ghost")
        add_sel_btn.setFixedHeight(26)
        add_sel_btn.setToolTip("将监控区选中的 JPG 加入此分组（其他组自动移除）")
        add_sel_btn.clicked.connect(
            lambda: self._request_add_selected(self._group.group_index)
        )
        header.addWidget(add_sel_btn)
```

Add signal and method to `_DraftGroupRow`:
```python
    add_selected_to_group = pyqtSignal(int)  # group_index

    def _request_add_selected(self, group_index: int) -> None:
        self.add_selected_to_group.emit(group_index)
```

In `GroupingPanel._rebuild()`, wire the signal:
```python
                row.add_selected_to_group.connect(self._on_add_selected_to_group)
```

Add to GroupingPanel:
```python
    def _on_add_selected_to_group(self, group_index: int) -> None:
        """Request monitor panel to add selected JPGs to this group.

        Emits add_to_group_requested(group_index, paths=[]) — caller must
        supply paths from the monitor panel's selection.
        """
        # Signal the workbench view to resolve the monitor selection
        self.add_selection_to_group_requested.emit(group_index)

    add_selection_to_group_requested = pyqtSignal(int)  # group_index
```

Update WorkbenchView wiring:
```python
        self._grouping.add_selection_to_group_requested.connect(self._on_add_selection_to_group)
```

Add handler:
```python
    def _on_add_selection_to_group(self, group_index: int) -> None:
        """Resolve monitor selection and add JPGs to group."""
        jpg_paths = self._monitor.selected_jpg_paths()
        if not jpg_paths:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, "加入分组", "请先在上方监控区选中要入组的 JPG。")
            return
        self._on_add_to_group(group_index, jpg_paths)
        self._monitor._on_select_none()
```

- [ ] **Step 7: Run tests**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && python -m pytest tests/test_workbench_view.py -x --tb=short -q 2>&1 | tail -10
```
Expected: all pass

- [ ] **Step 8: Commit**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && git add app/widgets/monitor_panel.py app/widgets/grouping_panel.py app/views/workbench_view.py tests/test_workbench_view.py && git commit -m "feat(workbench): add-to-group from monitor selection — mutual exclusion + manual-assign"
```

---

### Task 4: Remove JPG from group (right-click or button)

**Files:**
- Modify: `app/widgets/grouping_panel.py`
- Modify: `tests/test_workbench_view.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_workbench_view.py — add to TestGroupingPanel class:
def test_remove_jpg_from_group(self):
    """GroupingPanel.remove_jpg_from_group must remove path from the group."""
    from app.widgets.grouping_panel import GroupingPanel
    from app.services.grouping_service import Group, SpecimenGrouping
    ctx = _make_ctx()
    w = GroupingPanel(ctx)
    sg = SpecimenGrouping(
        uid="UID1",
        groups=[Group(group_index=0, jpg_paths=["/a.jpg", "/b.jpg"])],
    )
    w.load_grouping("UID1", sg)
    w.remove_jpg_from_group(0, "/a.jpg")
    assert "/a.jpg" not in w._grouping.groups[0].jpg_paths
    assert "/b.jpg" in w._grouping.groups[0].jpg_paths
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && python -m pytest "tests/test_workbench_view.py::TestGroupingPanel::test_remove_jpg_from_group" -xvs 2>&1 | tail -10
```
Expected: FAIL (method missing)

- [ ] **Step 3: Implement in GroupingPanel**

Add to `app/widgets/grouping_panel.py`:
```python
    def remove_jpg_from_group(self, group_index: int, jpg_path: str) -> None:
        """Remove *jpg_path* from the specified group.

        Mirrors web groupingRemoveFile() app.js:5274–5280.
        """
        if not self._grouping:
            return
        for g in self._grouping.groups:
            if g.group_index == group_index:
                g.jpg_paths = [p for p in g.jpg_paths if p != jpg_path]
                break
        self._rebuild()
        self.grouping_changed.emit()
```

Add delete button per JPG item in `_DraftGroupRow._setup_ui()` after the `_jpg_list`:

```python
        # Context menu on JPG list for removal
        self._jpg_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._jpg_list.customContextMenuRequested.connect(self._on_jpg_context_menu)
```

Add method to `_DraftGroupRow`:
```python
    jpg_remove_requested = pyqtSignal(int, str)  # group_index, jpg_path

    def _on_jpg_context_menu(self, pos) -> None:
        from PyQt6.QtWidgets import QMenu
        item = self._jpg_list.itemAt(pos)
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        menu = QMenu(self)
        action = menu.addAction("移除此 JPG")
        chosen = menu.exec(self._jpg_list.mapToGlobal(pos))
        if chosen == action:
            self.jpg_remove_requested.emit(self._group.group_index, path)
```

Wire in `GroupingPanel._rebuild()`:
```python
                row.jpg_remove_requested.connect(self._on_jpg_remove)
```

Add handler:
```python
    def _on_jpg_remove(self, group_index: int, jpg_path: str) -> None:
        self.remove_jpg_from_group(group_index, jpg_path)
```

- [ ] **Step 4: Run tests**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && python -m pytest tests/test_workbench_view.py -x --tb=short -q 2>&1 | tail -10
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && git add app/widgets/grouping_panel.py tests/test_workbench_view.py && git commit -m "feat(grouping): right-click remove JPG from group"
```

---

### Task 5: Helicon params panel widget

Creates a small `HeliconParamsPanel` widget (method A/B/C buttons + radius slider + smoothing slider) that is embedded in the right column, and whose values are passed to `stack_single_subprocess`.

**Files:**
- Create: `app/widgets/helicon_params_panel.py`
- Modify: `app/views/workbench_view.py`
- Modify: `tests/test_workbench_view.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_workbench_view.py — new class:
class TestHeliconParamsPanel:
    def test_constructs(self):
        from app.widgets.helicon_params_panel import HeliconParamsPanel
        w = HeliconParamsPanel()
        assert w is not None

    def test_default_params(self):
        from app.widgets.helicon_params_panel import HeliconParamsPanel
        w = HeliconParamsPanel()
        p = w.get_params()
        assert p["method"] in (0, 1, 2)
        assert 1 <= p["radius"] <= 30
        assert 1 <= p["smoothing"] <= 10

    def test_set_params(self):
        from app.widgets.helicon_params_panel import HeliconParamsPanel
        w = HeliconParamsPanel()
        w.set_params({"method": 1, "radius": 8, "smoothing": 4})
        p = w.get_params()
        assert p["method"] == 1
        assert p["radius"] == 8
        assert p["smoothing"] == 4
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && python -m pytest "tests/test_workbench_view.py::TestHeliconParamsPanel" -xvs 2>&1 | tail -10
```
Expected: FAIL (module missing)

- [ ] **Step 3: Create HeliconParamsPanel**

Create `app/widgets/helicon_params_panel.py`:

```python
"""helicon_params_panel.py — Helicon Focus parameter editor widget.

Mirrors the Helicon params side-panel in the web compose preview page
(app.js renderComposePage params section: method A/B/C + radius + smoothing).

Oracle: app.js:6884–6914 (compose page params panel).
"""
from __future__ import annotations
from typing import Optional
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton,
    QSlider, QVBoxLayout, QWidget,
)


class HeliconParamsPanel(QWidget):
    """Helicon Focus method/radius/smoothing editor.

    Signals
    -------
    params_changed()
        Emitted whenever any parameter changes.
    """

    params_changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._method: int = 0
        self._radius: float = 4.0
        self._smoothing: int = 4
        self._setup_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        sec = QFrame()
        sec.setObjectName("Panel")
        sec_lay = QVBoxLayout(sec)
        sec_lay.setContentsMargins(12, 8, 12, 10)
        sec_lay.setSpacing(8)

        title = QLabel("Helicon 参数")
        title.setObjectName("Section")
        sec_lay.addWidget(title)

        # Method A/B/C toggle buttons
        meth_row = QHBoxLayout()
        meth_row.setContentsMargins(0, 0, 0, 0)
        meth_row.setSpacing(4)
        meth_lbl = QLabel("方法")
        meth_lbl.setObjectName("MutedSmall")
        meth_row.addWidget(meth_lbl)
        self._method_btns: list[QPushButton] = []
        self._btn_group = QButtonGroup(self)
        self._btn_group.setExclusive(True)
        for i, label in enumerate(["A", "B", "C"]):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(i == self._method)
            btn.setFixedSize(32, 26)
            btn.setObjectName("Primary" if i == self._method else "Outline")
            self._btn_group.addButton(btn, i)
            self._method_btns.append(btn)
            meth_row.addWidget(btn)
        meth_row.addStretch()
        self._btn_group.idClicked.connect(self._on_method_changed)
        sec_lay.addLayout(meth_row)

        # Radius slider
        self._radius_slider, self._radius_lbl = self._make_slider(
            "半径 Radius", 10, 300, int(self._radius * 10), sec_lay
        )
        self._radius_slider.valueChanged.connect(self._on_radius_changed)

        # Smoothing slider
        self._smooth_slider, self._smooth_lbl = self._make_slider(
            "平滑 Smooth", 1, 10, self._smoothing, sec_lay
        )
        self._smooth_slider.valueChanged.connect(self._on_smooth_changed)

        root.addWidget(sec)
        root.addStretch()

    def _make_slider(
        self, label: str, min_val: int, max_val: int, init: int, parent_lay
    ) -> tuple[QSlider, QLabel]:
        row = QHBoxLayout()
        lbl_text = QLabel(label)
        lbl_text.setObjectName("MutedSmall")
        lbl_text.setFixedWidth(80)
        val_lbl = QLabel(str(init if label.startswith("平滑") else init / 10))
        val_lbl.setObjectName("Mono")
        val_lbl.setFixedWidth(30)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(min_val, max_val)
        slider.setValue(init)
        row.addWidget(lbl_text)
        row.addWidget(slider, stretch=1)
        row.addWidget(val_lbl)
        parent_lay.addLayout(row)
        return slider, val_lbl

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_method_changed(self, method_id: int) -> None:
        self._method = method_id
        for i, btn in enumerate(self._method_btns):
            btn.setObjectName("Primary" if i == method_id else "Outline")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self.params_changed.emit()

    def _on_radius_changed(self, value: int) -> None:
        self._radius = value / 10.0
        self._radius_lbl.setText(str(self._radius))
        self.params_changed.emit()

    def _on_smooth_changed(self, value: int) -> None:
        self._smoothing = value
        self._smooth_lbl.setText(str(value))
        self.params_changed.emit()

    # ── Public API ────────────────────────────────────────────────────────────

    def get_params(self) -> dict:
        """Return current params dict: {method: int, radius: float, smoothing: int}."""
        return {
            "method": self._method,
            "radius": self._radius,
            "smoothing": self._smoothing,
        }

    def set_params(self, params: dict) -> None:
        """Load params dict into the UI (does not emit params_changed)."""
        if "method" in params:
            self._method = int(params["method"])
            self._btn_group.button(self._method).setChecked(True)
            self._on_method_changed(self._method)
        if "radius" in params:
            self._radius = float(params["radius"])
            self._radius_slider.setValue(int(self._radius * 10))
        if "smoothing" in params:
            self._smoothing = int(params["smoothing"])
            self._smooth_slider.setValue(self._smoothing)
```

- [ ] **Step 4: Add HeliconParamsPanel to WorkbenchView right column**

In `app/views/workbench_view.py` `_setup_ui()`, after `right_lay.addWidget(self._naming)`:
```python
        self._helicon_params = HeliconParamsPanel()
        right_lay.addWidget(self._helicon_params)
```

Add import at top of file:
```python
from app.widgets.helicon_params_panel import HeliconParamsPanel
```

In `_on_compose_requested()`, pass params from `self._helicon_params.get_params()`:
```python
            p = self._helicon_params.get_params()
            result = stack_single_subprocess(
                jpg_paths=group.jpg_paths,
                output_file=output_path,
                method=str(p["method"]),
                radius=str(p["radius"]),
                smoothing=str(p["smoothing"]),
            )
```

- [ ] **Step 5: Run tests**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && python -m pytest tests/test_workbench_view.py -x --tb=short -q 2>&1 | tail -10
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && git add app/widgets/helicon_params_panel.py app/views/workbench_view.py tests/test_workbench_view.py && git commit -m "feat(workbench): HeliconParamsPanel — method/radius/smoothing, wired to compose"
```

---

### Task 6: Correct sequence naming on compose

When composing, `_on_compose_requested` must use `organize_preview()` to determine the next result sequence, name the TIFF `{uid}-{seq}.tif`, then bump the hint after success.

**Files:**
- Modify: `app/views/workbench_view.py`
- Modify: `tests/test_workbench_wiring.py` (new file)

- [ ] **Step 1: Write failing test (new file)**

Create `tests/test_workbench_wiring.py`:

```python
"""test_workbench_wiring.py — Tests for WorkbenchView logic that requires
real filesystem and DB (no Qt window needed for service-layer tests).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest


def _make_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS specimens (
            uid TEXT PRIMARY KEY, id TEXT, province TEXT, site TEXT, station TEXT,
            storage TEXT, collection_date TEXT, photo_date TEXT,
            scientific_name TEXT, scientific_name_cn TEXT,
            taxon_group TEXT, taxon_group_cn TEXT, order_name TEXT, order_cn TEXT,
            family TEXT, family_cn TEXT, genus TEXT, genus_cn TEXT,
            lon REAL, lat REAL, geo_area TEXT, collector TEXT, photographer TEXT,
            identifier TEXT, notes TEXT, photo_notes TEXT, angle TEXT,
            metadata INTEGER DEFAULT 0, pinned INTEGER DEFAULT 0,
            owner_project_dir TEXT, raw_json TEXT
        );
        CREATE TABLE IF NOT EXISTS tasks (
            uid TEXT PRIMARY KEY,
            is_active INTEGER DEFAULT 0, activated_at TEXT,
            last_organized_at TEXT, next_result_sequence_hint INTEGER, raw_json TEXT
        );
        CREATE TABLE IF NOT EXISTS grouping (
            uid TEXT, group_index INTEGER,
            angle_label TEXT, jpg_paths TEXT, composed_tiff_path TEXT,
            status TEXT, source TEXT, created_at TEXT, updated_at TEXT,
            result_sequence INTEGER, archive_zip TEXT, retired_tiff_paths TEXT, raw_json TEXT,
            PRIMARY KEY (uid, group_index)
        );
        CREATE TABLE IF NOT EXISTS explicit_unassigns (
            path TEXT PRIMARY KEY, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS seen_files (
            name TEXT PRIMARY KEY, first_seen_at TEXT
        );
    """)
    conn.commit()
    return conn


class TestSequenceNamingOnCompose:
    def test_organize_preview_names_first_tiff(self, tmp_path):
        """organize_preview must return seq=1 for a fresh uid with no existing TIFFs."""
        from app.services.organize_service import organize_preview
        db_path = str(tmp_path / "project.db")
        db = _make_db(db_path)
        results_dir = str(tmp_path / "results")
        os.makedirs(results_dir)
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        prev = organize_preview(db, uid, results_dir=results_dir)
        assert prev.next_seq == 1
        assert prev.suggested_tiff_name == "FJ-XM-B2-DLC001-1-T95E-20260601.tif"
        db.close()

    def test_organize_preview_increments_seq(self, tmp_path):
        """organize_preview must return seq=2 when seq-1 TIFF already exists."""
        from app.services.organize_service import organize_preview
        db_path = str(tmp_path / "project.db")
        db = _make_db(db_path)
        results_dir = str(tmp_path / "results")
        os.makedirs(results_dir)
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        # Create the seq-1 TIFF
        tiff1 = os.path.join(results_dir, "FJ-XM-B2-DLC001-1-T95E-20260601.tif")
        Path(tiff1).write_bytes(b"TIFF")
        prev = organize_preview(db, uid, results_dir=results_dir)
        assert prev.next_seq == 2
        assert prev.suggested_tiff_name == "FJ-XM-B2-DLC001-2-T95E-20260601.tif"
        db.close()
```

- [ ] **Step 2: Run to confirm tests pass (service logic already correct)**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && python -m pytest tests/test_workbench_wiring.py -xvs 2>&1 | tail -20
```
Expected: PASS (organize_service already implements this correctly)

- [ ] **Step 3: Verify `_on_compose_requested` uses organize_preview correctly**

Read `workbench_view.py:_on_compose_requested()`. It already calls:
```python
            preview = organize_preview(db, uid, results_dir, incoming_dir)
            output_name = preview.suggested_tiff_name
            output_path = os.path.join(results_dir, output_name)
```

But it does NOT bump the sequence hint after compose. Add after `save_grouping(db, uid, grouping.groups)`:
```python
                # Bump next_result_sequence_hint so next compose gets seq+1
                try:
                    from app.services.organize_service import _bump_seq_hint
                    _bump_seq_hint(db, uid, preview.next_seq)
                except Exception:
                    pass
```

Also store `result_sequence` on the group:
```python
                group.result_sequence = preview.next_seq
```

- [ ] **Step 4: Add test that verifies hint is bumped**

Add to `tests/test_workbench_wiring.py`:

```python
class TestSeqHintBump:
    def test_bump_seq_hint_updates_db(self, tmp_path):
        """_bump_seq_hint must advance next_result_sequence_hint."""
        from app.services.organize_service import _bump_seq_hint
        db_path = str(tmp_path / "project.db")
        db = _make_db(db_path)
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        _bump_seq_hint(db, uid, 1)
        row = db.execute(
            "SELECT next_result_sequence_hint FROM tasks WHERE uid = ?", (uid,)
        ).fetchone()
        assert row is not None
        assert row[0] == 2  # 1 + 1
        db.close()
```

- [ ] **Step 5: Run all tests**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && python -m pytest tests/test_workbench_view.py tests/test_workbench_wiring.py -x --tb=short -q 2>&1 | tail -10
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && git add app/views/workbench_view.py tests/test_workbench_wiring.py && git commit -m "feat(workbench): bump seq hint after compose + store result_sequence on group"
```

---

### Task 7: Free compose (无号合成) from monitor selection

Adds a "无号合成" option in the GroupingPanel "⋯ 更多" menu.
When triggered: selected JPGs from monitor → Helicon → TIFF saved to incoming-jpg/ with auto-name.

**Files:**
- Modify: `app/widgets/grouping_panel.py`
- Modify: `app/views/workbench_view.py`
- Modify: `tests/test_workbench_wiring.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_workbench_wiring.py — new class:
class TestFreeCompose:
    def test_free_compose_names_output_in_incoming(self, tmp_path):
        """Free compose output basename must start with '自由合成-' if no name given."""
        incoming_dir = str(tmp_path / "incoming-jpg")
        os.makedirs(incoming_dir)
        # The output path pattern is: incoming-jpg/自由合成-1.tif (or user name)
        from app.views.workbench_view import _free_compose_output_name
        name1 = _free_compose_output_name(incoming_dir, None)
        assert name1.startswith("自由合成-")
        assert name1.endswith(".tif")
        # Create first file to test increment
        Path(os.path.join(incoming_dir, name1)).write_bytes(b"X")
        name2 = _free_compose_output_name(incoming_dir, None)
        assert name2 != name1
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && python -m pytest "tests/test_workbench_wiring.py::TestFreeCompose" -xvs 2>&1 | tail -10
```
Expected: FAIL (function missing)

- [ ] **Step 3: Implement _free_compose_output_name in workbench_view**

Add module-level function to `app/views/workbench_view.py`:

```python
def _free_compose_output_name(incoming_dir: str, user_name: str | None) -> str:
    """Return a unique output TIFF name for free-compose.

    If user_name is given, sanitize and use it.
    Otherwise auto-generate "自由合成-N.tif" incrementing N until no conflict.
    Oracle: app.js freeComposeSelected(), auto-naming "自由合成-N".
    """
    import re
    if user_name:
        safe = re.sub(r'[\\/:*?"<>|]', "_", user_name.strip())
        if safe and not safe.lower().endswith(".tif"):
            safe += ".tif"
        if safe and not os.path.exists(os.path.join(incoming_dir, safe)):
            return safe
    n = 1
    while True:
        candidate = f"自由合成-{n}.tif"
        if not os.path.exists(os.path.join(incoming_dir, candidate)):
            return candidate
        n += 1
```

- [ ] **Step 4: Add "无号合成" action to GroupingPanel ⋯ more menu**

In `app/widgets/grouping_panel.py`, add signal:
```python
    free_compose_requested = pyqtSignal()   # triggered from ⋯ menu
```

Wire `more_btn` to show a QMenu:
```python
        more_btn.setMenu(self._build_more_menu())
```

Add method:
```python
    def _build_more_menu(self):
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        free_act = menu.addAction("无号合成（选中 JPG → incoming-jpg/）")
        free_act.triggered.connect(self.free_compose_requested.emit)
        retro_act = menu.addAction("存量整理…")
        retro_act.triggered.connect(self.retroactive_requested.emit)
        return menu

    retroactive_requested = pyqtSignal()
```

- [ ] **Step 5: Wire in WorkbenchView**

```python
        self._grouping.free_compose_requested.connect(self._on_free_compose)
        self._grouping.retroactive_requested.connect(self._on_retroactive_scan)
```

Add handler:
```python
    def _on_free_compose(self) -> None:
        """Free compose: selected monitor JPGs → Helicon → incoming-jpg/.

        Oracle: app.js freeComposeSelected() app.js:7982–8010.
        """
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            QMessageBox.information(self, "无号合成", "请先打开一个项目。")
            return

        jpg_paths = self._monitor.selected_jpg_paths()
        if not jpg_paths:
            QMessageBox.information(self, "无号合成", "请先在监控区选中要合成的 JPG。")
            return

        from app.services.helicon_service import detect_helicon, stack_single_subprocess
        exe = detect_helicon()
        if not exe:
            QMessageBox.warning(self, "未检测到 Helicon Focus",
                                "请确认 Helicon Focus 已安装并配置路径。")
            return

        from PyQt6.QtWidgets import QInputDialog
        user_name, ok = QInputDialog.getText(
            self, "无号合成", "输出文件名（留空自动命名）：", text=""
        )
        if not ok:
            return

        incoming_dir = os.path.join(project_dir, "incoming-jpg")
        os.makedirs(incoming_dir, exist_ok=True)
        output_name = _free_compose_output_name(incoming_dir, user_name.strip() or None)
        output_path = os.path.join(incoming_dir, output_name)

        params = self._helicon_params.get_params()
        progress = QProgressDialog(
            f"无号合成 {len(jpg_paths)} 张 JPG…", None, 0, 0, self
        )
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setWindowTitle("Helicon 无号合成")
        progress.show()
        QApplication.processEvents()
        try:
            result = stack_single_subprocess(
                jpg_paths=jpg_paths,
                output_file=output_path,
                method=str(params["method"]),
                radius=str(params["radius"]),
                smoothing=str(params["smoothing"]),
            )
        finally:
            progress.close()

        if result.get("ok") and os.path.isfile(output_path):
            QMessageBox.information(self, "无号合成完成", f"TIFF 已保存到 incoming-jpg/：\n{output_name}")
            self._refresh_monitor()
        else:
            QMessageBox.warning(self, "无号合成失败", "Helicon 执行后未生成输出文件。")
```

- [ ] **Step 6: Run tests**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && python -m pytest tests/ -x --tb=short -q 2>&1 | tail -10
```
Expected: all pass

- [ ] **Step 7: Commit**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && git add app/widgets/grouping_panel.py app/views/workbench_view.py tests/test_workbench_wiring.py && git commit -m "feat(workbench): free compose (无号合成) — selected JPGs → incoming-jpg/"
```

---

### Task 8: Retroactive organize modal

Creates a modal dialog mirroring `renderRetroactiveModal()` app.js:8113–8198.
It calls `organize_service.retroactive_scan()` (or server endpoint equivalent — since we're not calling the Node server, we implement the logic directly via existing services).

**Note:** The retroactive scan/apply logic is currently only in `server.js` (not in a Python service). We add a minimal Python stub that calls `organize_service` and `archive_service` directly.

**Files:**
- Create: `app/widgets/retroactive_modal.py`
- Create: `app/services/retroactive_service.py`
- Modify: `app/views/workbench_view.py`
- Create: `tests/test_retroactive_service.py`

- [ ] **Step 1: Write failing test for retroactive_service**

Create `tests/test_retroactive_service.py`:

```python
"""test_retroactive_service.py — Tests for retroactive organize scan logic."""
from __future__ import annotations
import os
import sqlite3
from pathlib import Path
import pytest


def _make_db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "project.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS seen_files (name TEXT PRIMARY KEY, first_seen_at TEXT);
        CREATE TABLE IF NOT EXISTS tasks (
            uid TEXT PRIMARY KEY, is_active INTEGER DEFAULT 0,
            activated_at TEXT, last_organized_at TEXT,
            next_result_sequence_hint INTEGER, raw_json TEXT
        );
        CREATE TABLE IF NOT EXISTS grouping (
            uid TEXT, group_index INTEGER,
            angle_label TEXT, jpg_paths TEXT, composed_tiff_path TEXT,
            status TEXT, source TEXT, created_at TEXT, updated_at TEXT,
            result_sequence INTEGER, archive_zip TEXT, retired_tiff_paths TEXT, raw_json TEXT,
            PRIMARY KEY (uid, group_index)
        );
    """)
    conn.commit()
    return conn


class TestRetroactiveScan:
    def test_scan_finds_named_tiffs(self, tmp_path):
        """scan_project_retroactive must return groups for each named TIFF."""
        from app.services.retroactive_service import scan_project_retroactive
        project_dir = str(tmp_path)
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        incoming_dir = tmp_path / "incoming-jpg"
        incoming_dir.mkdir()
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        tiff_name = "FJ-XM-B2-DLC001-1-T95E-20260601.tif"
        (results_dir / tiff_name).write_bytes(b"TIFF")
        db = _make_db(tmp_path)
        result = scan_project_retroactive(project_dir, db)
        assert any(sp["uid"] == uid for sp in result["specimens"])
        db.close()

    def test_scan_finds_jpgs_before_tiff(self, tmp_path):
        """Scan must associate JPGs with the TIFF that was written after them."""
        from app.services.retroactive_service import scan_project_retroactive
        import time
        project_dir = str(tmp_path)
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        incoming_dir = tmp_path / "incoming-jpg"
        incoming_dir.mkdir()
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        tiff_name = "FJ-XM-B2-DLC001-1-T95E-20260601.tif"
        # Create JPG first (earlier mtime)
        jpg_path = incoming_dir / "IMG_001.jpg"
        jpg_path.write_bytes(b"JFIF")
        time.sleep(0.05)
        # Then create TIFF (later mtime)
        (results_dir / tiff_name).write_bytes(b"TIFF")
        db = _make_db(tmp_path)
        result = scan_project_retroactive(project_dir, db)
        specimens = result["specimens"]
        assert specimens, "Must find at least one specimen"
        groups = specimens[0]["groups"]
        assert groups, "Must find at least one group"
        assert groups[0]["jpgCount"] >= 1
        db.close()
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && python -m pytest tests/test_retroactive_service.py -xvs 2>&1 | tail -10
```
Expected: FAIL (module missing)

- [ ] **Step 3: Create retroactive_service.py**

Create `app/services/retroactive_service.py`:

```python
"""retroactive_service.py — Retroactive organize: scan results/ + incoming-jpg/.

Oracle: server.js POST /api/organize/retroactive/scan (lines ~3840-3920 area)
        and POST /api/organize/retroactive/apply.

Algorithm (matches server.js exactly):
  1. List all TIFF files in results/ with valid 7-part naming.
  2. For each TIFF, collect JPGs from incoming-jpg/ whose mtime is EARLIER
     than the TIFF's mtime (time-window pairing: "JPGs shot before this TIF").
  3. Return specimens → groups structure with jpgCount.

apply:
  For each confirmed group, call archive_service.archive_group.
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.services.organize_service import _parse_uid_from_tiff_name


def _iso_mtime(p: str) -> str:
    st = os.stat(p)
    return datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()


def scan_project_retroactive(
    project_dir: str,
    db: sqlite3.Connection,
    *,
    incoming_subdir: str = "incoming-jpg",
    results_subdir: str = "results",
    selection_paths: Optional[list[str]] = None,
) -> dict:
    """Scan project for named TIFFs and pair with preceding JPGs.

    Returns dict with keys:
      specimens: list of {uid, groups: [{seq, tiffName, tiffPath, jpgPaths, jpgCount}]}
      unassignedJpgs: list of paths with no TIFF pair
      unnamedTiffs: list of {name} for TIFFs that don't match naming convention

    Oracle: server.js /api/organize/retroactive/scan handler.
    """
    resolved = str(Path(project_dir).resolve())
    results_dir = os.path.join(resolved, results_subdir)
    incoming_dir = os.path.join(resolved, incoming_subdir)

    # List TIFFs in results/
    tiff_files: list[dict] = []
    unnamed_tiffs: list[dict] = []
    if os.path.isdir(results_dir):
        for name in sorted(os.listdir(results_dir)):
            if not re.search(r"\.tiff?$", name, re.IGNORECASE):
                continue
            full = os.path.join(results_dir, name)
            if not os.path.isfile(full):
                continue
            uid = _parse_uid_from_tiff_name(name)
            if not uid:
                unnamed_tiffs.append({"name": name, "path": full})
                continue
            stem = Path(name).stem
            parts = stem.split("-")
            try:
                seq = int(parts[4])
            except (IndexError, ValueError):
                unnamed_tiffs.append({"name": name, "path": full})
                continue
            tiff_files.append({
                "uid": uid, "seq": seq, "name": name, "path": full,
                "mtime": _iso_mtime(full),
            })

    # If manual selection mode, filter to only TIFFs in selection_paths
    if selection_paths:
        sel_set = {os.path.abspath(p) for p in selection_paths}
        tiff_files = [t for t in tiff_files if os.path.abspath(t["path"]) in sel_set]

    if not tiff_files:
        return {"specimens": [], "unassignedJpgs": [], "unnamedTiffs": unnamed_tiffs, "ok": True}

    # Sort TIFFs by mtime ascending (needed for time-window algorithm)
    tiff_files.sort(key=lambda t: t["mtime"])

    # List JPGs in incoming-jpg/
    jpg_files: list[dict] = []
    if os.path.isdir(incoming_dir):
        for name in os.listdir(incoming_dir):
            if not re.search(r"\.jpe?g$", name, re.IGNORECASE):
                continue
            full = os.path.join(incoming_dir, name)
            if not os.path.isfile(full):
                continue
            if selection_paths:
                if os.path.abspath(full) not in sel_set:
                    continue
            jpg_files.append({"name": name, "path": full, "mtime": _iso_mtime(full)})

    jpg_files.sort(key=lambda j: j["mtime"])

    # Time-window pairing: each JPG is assigned to the first TIFF with mtime > jpg mtime
    jpg_to_tiff: dict[str, int] = {}  # jpg_path → tiff index
    for ji, jpg in enumerate(jpg_files):
        for ti, tiff in enumerate(tiff_files):
            if tiff["mtime"] > jpg["mtime"]:
                jpg_to_tiff[jpg["path"]] = ti
                break  # first TIFF after this JPG

    # Group by TIFF
    tiff_groups: dict[int, list[str]] = {i: [] for i in range(len(tiff_files))}
    for jpg_path, tiff_idx in jpg_to_tiff.items():
        tiff_groups[tiff_idx].append(jpg_path)

    unassigned_jpgs = [j["path"] for j in jpg_files if j["path"] not in jpg_to_tiff]

    # Build specimens structure
    uid_to_groups: dict[str, list[dict]] = {}
    for ti, tiff in enumerate(tiff_files):
        uid = tiff["uid"]
        if uid not in uid_to_groups:
            uid_to_groups[uid] = []
        jpg_paths = tiff_groups[ti]
        uid_to_groups[uid].append({
            "seq": tiff["seq"],
            "tiffName": tiff["name"],
            "tiffPath": tiff["path"],
            "jpgPaths": jpg_paths,
            "jpgCount": len(jpg_paths),
        })

    specimens = [
        {"uid": uid, "groups": sorted(groups, key=lambda g: g["seq"])}
        for uid, groups in uid_to_groups.items()
    ]

    return {
        "ok": True,
        "specimens": specimens,
        "unassignedJpgs": unassigned_jpgs,
        "unnamedTiffs": unnamed_tiffs,
    }
```

- [ ] **Step 4: Create retroactive_modal.py**

Create `app/widgets/retroactive_modal.py`:

```python
"""retroactive_modal.py — Retroactive organize dialog.

Shows scan results (specimens + groups with JPG counts), lets user select/deselect
groups, and confirm to archive.  Mirrors renderRetroactiveModal() app.js:8113–8198.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Optional, TYPE_CHECKING
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

if TYPE_CHECKING:
    from app.app_context import AppContext


class RetroactiveModal(QDialog):
    """Retroactive organize: show scan result, confirm → archive groups."""

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
                txt = (
                    f"成果 #{g['seq']}  {g['tiffName']}  ← "
                    f"{g['jpgCount']} 张原片" if g["jpgCount"] > 0
                    else f"成果 #{g['seq']}  {g['tiffName']}  ← ⚠ 没配到原片（不可打包）"
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

        ok_count, fail_count = 0, 0
        for uid, g in to_archive:
            try:
                archive_group(
                    jpg_paths=g["jpgPaths"],
                    tiff_path=g["tiffPath"],
                    project_dir=project_dir,
                    delete_jpg=self._delete_jpg,
                )
                ok_count += 1
            except Exception as e:
                fail_count += 1

        msg = f"存量整理完成：{ok_count} 组归档"
        if fail_count:
            msg += f"，{fail_count} 组失败"
        QMessageBox.information(self, "整理完成", msg)
        self.accept()
```

- [ ] **Step 5: Wire retroactive in WorkbenchView**

Add handler to `app/views/workbench_view.py`:
```python
    def _on_retroactive_scan(self) -> None:
        """Launch retroactive organize modal.

        Oracle: app.js retroactiveScan() + renderRetroactiveModal().
        """
        project_dir = self.ctx.current_project_dir
        db = self.ctx.get_db()
        if not project_dir or not db:
            QMessageBox.information(self, "存量整理", "请先打开一个项目。")
            return

        try:
            from app.services.retroactive_service import scan_project_retroactive
            result = scan_project_retroactive(project_dir, db)
        except Exception as exc:
            QMessageBox.warning(self, "扫描失败", str(exc))
            return

        total_groups = sum(len(sp["groups"]) for sp in result.get("specimens", []))
        if not total_groups and not result.get("unnamedTiffs"):
            QMessageBox.information(
                self, "存量整理",
                "没找到可整理的 TIF 成片（需 results/ 里有按编号命名的 TIF）。"
            )
            return

        from app.widgets.retroactive_modal import RetroactiveModal
        dlg = RetroactiveModal(self.ctx, result, parent=self)
        if dlg.exec() == RetroactiveModal.DialogCode.Accepted:
            self._refresh_monitor()
```

Add import at top:
```python
from PyQt6.QtWidgets import QInputDialog
```

- [ ] **Step 6: Run all tests**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && python -m pytest tests/ -x --tb=short -q 2>&1 | tail -10
```
Expected: all pass

- [ ] **Step 7: Commit**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && git add app/services/retroactive_service.py app/widgets/retroactive_modal.py app/widgets/grouping_panel.py app/views/workbench_view.py tests/test_retroactive_service.py && git commit -m "feat(workbench): retroactive organize — scan+modal+apply (存量整理)"
```

---

### Task 9: Project settings drawer (Helicon config + auto-activate toggle)

Creates `ProjectSettingsDrawer` QWidget (overlay drawer, not modal) for:
- Helicon Focus path configuration (detect + manual path input)
- Auto-activate on new specimen toggle
- Display of incomingJpgSubdir / resultsSubdir paths

**Files:**
- Create: `app/widgets/project_settings_drawer.py`
- Modify: `app/views/workbench_view.py`
- Modify: `tests/test_workbench_view.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_workbench_view.py — new class:
class TestProjectSettingsDrawer:
    def test_constructs(self):
        from app.widgets.project_settings_drawer import ProjectSettingsDrawer
        ctx = _make_ctx()
        w = ProjectSettingsDrawer(ctx)
        assert w is not None

    def test_has_helicon_status_label(self):
        from app.widgets.project_settings_drawer import ProjectSettingsDrawer
        ctx = _make_ctx()
        w = ProjectSettingsDrawer(ctx)
        assert hasattr(w, "_helicon_status_lbl")

    def test_has_auto_activate_checkbox(self):
        from app.widgets.project_settings_drawer import ProjectSettingsDrawer
        ctx = _make_ctx()
        w = ProjectSettingsDrawer(ctx)
        assert hasattr(w, "_auto_activate_cb")
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && python -m pytest "tests/test_workbench_view.py::TestProjectSettingsDrawer" -xvs 2>&1 | tail -10
```
Expected: FAIL

- [ ] **Step 3: Create ProjectSettingsDrawer**

Create `app/widgets/project_settings_drawer.py`:

```python
"""project_settings_drawer.py — Project settings side drawer.

Mirrors renderProjectSettingsDrawer() (app.js:9418) and
renderHeliconConfigModal() (app.js:7028).

Contains:
  - Helicon Focus path detection + manual override
  - Auto-activate on new specimen toggle
  - Read-only display of incomingJpgSubdir / resultsSubdir
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Optional, TYPE_CHECKING
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout, QWidget,
)

if TYPE_CHECKING:
    from app.app_context import AppContext


class ProjectSettingsDrawer(QWidget):
    """Overlay drawer for project + Helicon settings.

    Show by calling .show(); hide with .hide().
    """

    closed = pyqtSignal()
    helicon_path_changed = pyqtSignal(str)   # new exe path

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("SettingsDrawer")
        self._setup_ui()
        self.hide()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        # Header
        head = QHBoxLayout()
        title = QLabel("项目设置")
        title.setObjectName("WorkspaceTitle")
        head.addWidget(title)
        head.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setObjectName("Ghost")
        close_btn.setFixedSize(28, 28)
        close_btn.clicked.connect(self._on_close)
        head.addWidget(close_btn)
        root.addLayout(head)

        sep = QFrame(); sep.setObjectName("Divider"); sep.setFixedHeight(1)
        root.addWidget(sep)

        # ── Helicon section ────────────────────────────────────────────────────
        hel_title = QLabel("Helicon Focus 配置")
        hel_title.setObjectName("Section")
        root.addWidget(hel_title)

        self._helicon_status_lbl = QLabel("检测中…")
        self._helicon_status_lbl.setObjectName("MutedSmall")
        self._helicon_status_lbl.setWordWrap(True)
        root.addWidget(self._helicon_status_lbl)

        path_row = QHBoxLayout()
        self._helicon_path_edit = QLineEdit()
        self._helicon_path_edit.setPlaceholderText("自定义 Helicon.exe 路径（留空=自动检测）")
        self._helicon_path_edit.setFixedHeight(30)
        path_row.addWidget(self._helicon_path_edit)
        detect_btn = QPushButton("检测")
        detect_btn.setObjectName("Outline")
        detect_btn.setFixedSize(52, 30)
        detect_btn.clicked.connect(self._on_detect_helicon)
        path_row.addWidget(detect_btn)
        root.addLayout(path_row)

        # ── Project paths (read-only) ─────────────────────────────────────────
        sep2 = QFrame(); sep2.setObjectName("Divider"); sep2.setFixedHeight(1)
        root.addWidget(sep2)

        proj_title = QLabel("工作目录子目录")
        proj_title.setObjectName("Section")
        root.addWidget(proj_title)

        self._dir_info_lbl = QLabel("（未选择项目）")
        self._dir_info_lbl.setObjectName("MutedSmall")
        self._dir_info_lbl.setWordWrap(True)
        root.addWidget(self._dir_info_lbl)

        # ── Auto-activate toggle ──────────────────────────────────────────────
        sep3 = QFrame(); sep3.setObjectName("Divider"); sep3.setFixedHeight(1)
        root.addWidget(sep3)

        self._auto_activate_cb = QCheckBox("新建编号后自动激活")
        self._auto_activate_cb.setChecked(False)
        self._auto_activate_cb.toggled.connect(self._on_auto_activate_changed)
        root.addWidget(self._auto_activate_cb)

        root.addStretch()

    # ── Public ────────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Update Helicon status + dir info from current state."""
        try:
            from app.services.helicon_service import detect_helicon, reset_helicon_cache
            reset_helicon_cache()
            exe = detect_helicon()
            if exe:
                self._helicon_status_lbl.setText(f"✅ 已检测到：{exe}")
            else:
                self._helicon_status_lbl.setText(
                    "⚠️ 未检测到 Helicon Focus。请安装后重新检测，"
                    "或在下方填写自定义路径。"
                )
        except Exception as e:
            self._helicon_status_lbl.setText(f"检测失败：{e}")

        project_dir = getattr(self.ctx, "current_project_dir", None)
        if project_dir:
            from app.services.project_service import INCOMING_JPG_DIR, RESULTS_DIR
            self._dir_info_lbl.setText(
                f"相机 JPG：{INCOMING_JPG_DIR}/\n成果 TIFF/ZIP：{RESULTS_DIR}/"
            )
        else:
            self._dir_info_lbl.setText("（未选择项目）")

        # Load auto-activate setting from ctx.settings
        try:
            val = bool(getattr(self.ctx.settings, "auto_activate_on_new_specimen", False))
            self._auto_activate_cb.setChecked(val)
        except Exception:
            pass

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_close(self) -> None:
        self.hide()
        self.closed.emit()

    def _on_detect_helicon(self) -> None:
        custom_path = self._helicon_path_edit.text().strip()
        if custom_path:
            os.environ["HELICON_FOCUS_PATH"] = custom_path
        self.refresh()
        if custom_path:
            self.helicon_path_changed.emit(custom_path)

    def _on_auto_activate_changed(self, checked: bool) -> None:
        try:
            self.ctx.settings.auto_activate_on_new_specimen = checked
        except Exception:
            pass
```

- [ ] **Step 4: Add settings button to WorkbenchView header + wire drawer**

In `app/views/workbench_view.py` `_build_header()`, add settings button:
```python
        settings_btn = QPushButton("⚙ 设置")
        settings_btn.setObjectName("Ghost")
        settings_btn.setFixedHeight(28)
        settings_btn.clicked.connect(self._on_open_settings)
        row.addWidget(settings_btn)
```

In `_setup_ui()`, create the drawer (overlay-style):
```python
        from app.widgets.project_settings_drawer import ProjectSettingsDrawer
        self._settings_drawer = ProjectSettingsDrawer(self.ctx, parent=self)
        self._settings_drawer.setFixedWidth(380)
```

Add handler:
```python
    def _on_open_settings(self) -> None:
        self._settings_drawer.refresh()
        self._settings_drawer.show()
        # Position at right edge of window
        try:
            win_rect = self.rect()
            dw = self._settings_drawer
            dw.setGeometry(
                win_rect.right() - dw.width(), 0,
                dw.width(), win_rect.height()
            )
        except Exception:
            pass
```

- [ ] **Step 5: Add auto_activate_on_new_specimen to AppSettings**

In `app/config/settings.py`, add:
```python
    @property
    def auto_activate_on_new_specimen(self) -> bool:
        return self._qs.value("workbench/auto_activate_on_new_specimen", False, type=bool)

    @auto_activate_on_new_specimen.setter
    def auto_activate_on_new_specimen(self, val: bool) -> None:
        self._qs.setValue("workbench/auto_activate_on_new_specimen", val)
```

- [ ] **Step 6: Run tests**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && python -m pytest tests/test_workbench_view.py -x --tb=short -q 2>&1 | tail -10
```
Expected: all pass

- [ ] **Step 7: Commit**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && git add app/widgets/project_settings_drawer.py app/views/workbench_view.py app/config/settings.py tests/test_workbench_view.py && git commit -m "feat(workbench): project settings drawer — Helicon config + auto-activate toggle"
```

---

### Task 10: Add-JPG button in monitor panel + open-in-explorer for results

**Files:**
- Modify: `app/widgets/monitor_panel.py`
- Modify: `app/widgets/results_column.py`
- Modify: `tests/test_workbench_view.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_workbench_view.py — new class:
class TestMonitorPanelAddJpg:
    def test_has_add_jpg_signal(self):
        """MonitorPanel must emit add_jpg_requested signal."""
        from app.widgets.monitor_panel import MonitorPanel
        ctx = _make_ctx()
        w = MonitorPanel(ctx)
        assert hasattr(w, "add_jpg_requested")

class TestResultsColumnOpenExplorer:
    def test_load_uid_with_open_btn(self):
        """ResultsColumn items must have an 'open in folder' mechanism."""
        from app.widgets.results_column import ResultsColumn
        w = ResultsColumn()
        tiffs = [{"path": "/fake/result.tif", "name": "result.tif"}]
        w.load_uid("UID1", tiffs, [])
        assert hasattr(w, "_open_in_explorer")
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && python -m pytest "tests/test_workbench_view.py::TestMonitorPanelAddJpg" "tests/test_workbench_view.py::TestResultsColumnOpenExplorer" -xvs 2>&1 | tail -10
```

- [ ] **Step 3: Add add_jpg_requested signal to MonitorPanel**

In `app/widgets/monitor_panel.py` class body (signals):
```python
    add_jpg_requested = pyqtSignal()   # emitted when user clicks "添加照片"
```

In the controls bar section of `_setup_ui()` (after the refresh button):
```python
        add_btn = QPushButton("添加照片")
        add_btn.setObjectName("Outline")
        add_btn.setFixedHeight(30)
        add_btn.setToolTip("选择 JPG 照片导入 incoming-jpg/（拖放或文件选择器）")
        add_btn.clicked.connect(self.add_jpg_requested.emit)
        # insert into existing controls bar
```

Wire in `WorkbenchView._setup_ui()`:
```python
        self._monitor.add_jpg_requested.connect(self._on_add_jpg_files)
```

Add handler using `QFileDialog`:
```python
    def _on_add_jpg_files(self) -> None:
        """Open file picker for JPGs → copy to incoming-jpg/.

        Oracle: app.js importJpgFiles() app.js:7944–7975.
        """
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            QMessageBox.information(self, "添加照片", "请先打开一个项目。")
            return

        from PyQt6.QtWidgets import QFileDialog
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择 JPG 照片",
            filter="JPG 照片 (*.jpg *.jpeg *.JPG *.JPEG)",
        )
        if not paths:
            return

        incoming_dir = os.path.join(project_dir, "incoming-jpg")
        os.makedirs(incoming_dir, exist_ok=True)
        import shutil
        errors = []
        for src in paths:
            dest = os.path.join(incoming_dir, os.path.basename(src))
            try:
                if os.path.abspath(src) != os.path.abspath(dest):
                    shutil.copy2(src, dest)
            except OSError as e:
                errors.append(str(e))

        if errors:
            QMessageBox.warning(self, "导入部分失败", "\n".join(errors[:5]))
        self._refresh_monitor()
```

- [ ] **Step 4: Add _open_in_explorer to ResultsColumn**

In `app/widgets/results_column.py`, add:
```python
    def _open_in_explorer(self, path: str) -> None:
        """Open the folder containing *path* in the system file explorer."""
        import subprocess
        import sys
        folder = os.path.dirname(path) if os.path.isfile(path) else path
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                # WSL: open Windows Explorer via wslpath
                win_path = path
                try:
                    from app.utils.path_utils import wsl_to_windows
                    win_path = wsl_to_windows(path) or path
                except Exception:
                    pass
                subprocess.Popen(["explorer.exe", "/select,", win_path])
        except Exception:
            pass
```

And add an "在文件夹中显示" button to each item in `load_uid`:

In `ResultsColumn.load_uid()`, for each TIFF item:
```python
            open_btn = QPushButton("📂")
            open_btn.setObjectName("Ghost")
            open_btn.setFixedSize(26, 26)
            open_btn.setToolTip("在文件夹中显示")
            open_btn.clicked.connect(lambda _, p=t["path"]: self._open_in_explorer(p))
            row.addWidget(open_btn)
```

- [ ] **Step 5: Run tests**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && python -m pytest tests/test_workbench_view.py -x --tb=short -q 2>&1 | tail -10
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && git add app/widgets/monitor_panel.py app/widgets/results_column.py app/views/workbench_view.py tests/test_workbench_view.py && git commit -m "feat(workbench): add-JPG file picker + open-in-explorer for results"
```

---

### Task 11: Full test suite pass + themed screenshot

- [ ] **Step 1: Run full test suite**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && python -m pytest tests/ --tb=short -q 2>&1 | tail -20
```
Expected: all pass (0 failures, some skips allowed for Helicon/JXL)

- [ ] **Step 2: Generate themed workbench screenshot with sample data**

Create `docs/shots/capture_workbench_func.py` (following the pattern of other capture scripts):

```python
"""capture_workbench_func.py — Screenshot of the fully-functional workbench."""
import os, sys, sqlite3
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)

# Load theme
from app.config.theme import apply_theme
apply_theme(app)

# Create AppContext with temp project + sample data
import tempfile
tmp = tempfile.mkdtemp(prefix="wb-shot-")
project_dir = os.path.join(tmp, "demo-project")
os.makedirs(os.path.join(project_dir, "incoming-jpg"))
os.makedirs(os.path.join(project_dir, "results"))
os.makedirs(os.path.join(project_dir, "_data"))

conn = sqlite3.connect(os.path.join(project_dir, "_data", "project.db"))
conn.row_factory = sqlite3.Row
conn.executescript("""
    CREATE TABLE IF NOT EXISTS specimens (
        uid TEXT PRIMARY KEY, id TEXT, province TEXT, site TEXT, station TEXT,
        storage TEXT, collection_date TEXT, photo_date TEXT,
        scientific_name TEXT, scientific_name_cn TEXT,
        taxon_group TEXT, taxon_group_cn TEXT, order_name TEXT, order_cn TEXT,
        family TEXT, family_cn TEXT, genus TEXT, genus_cn TEXT,
        lon REAL, lat REAL, geo_area TEXT, collector TEXT, photographer TEXT,
        identifier TEXT, notes TEXT, photo_notes TEXT, angle TEXT,
        metadata INTEGER DEFAULT 0, pinned INTEGER DEFAULT 0,
        owner_project_dir TEXT, raw_json TEXT
    );
    CREATE TABLE IF NOT EXISTS tasks (
        uid TEXT PRIMARY KEY, is_active INTEGER DEFAULT 0, activated_at TEXT,
        last_organized_at TEXT, next_result_sequence_hint INTEGER, raw_json TEXT
    );
    CREATE TABLE IF NOT EXISTS grouping (
        uid TEXT, group_index INTEGER, angle_label TEXT, jpg_paths TEXT,
        composed_tiff_path TEXT, status TEXT, source TEXT, created_at TEXT,
        updated_at TEXT, result_sequence INTEGER, archive_zip TEXT,
        retired_tiff_paths TEXT, raw_json TEXT,
        PRIMARY KEY (uid, group_index)
    );
    CREATE TABLE IF NOT EXISTS explicit_unassigns (path TEXT PRIMARY KEY, created_at TEXT);
    CREATE TABLE IF NOT EXISTS seen_files (name TEXT PRIMARY KEY, first_seen_at TEXT);
""")
# Add sample specimens
for uid, sci in [
    ("FJ-YGLZ-B1-DLC001-RD75E-20260601", "Conus textile"),
    ("FJ-YGLZ-B2-DLC002-T95E-20260601",  "Murex brandaris"),
    ("FJ-YGLZ-B3-LPS001-T70E-20260601",  "Haliotis asinina"),
]:
    conn.execute(
        "INSERT OR IGNORE INTO specimens (uid, scientific_name, owner_project_dir) VALUES (?,?,?)",
        (uid, sci, project_dir)
    )
# Activate first specimen
conn.execute(
    "INSERT OR IGNORE INTO tasks (uid, is_active, activated_at) VALUES (?,1,'2026-06-03T09:00:00')",
    ("FJ-YGLZ-B1-DLC001-RD75E-20260601",)
)
conn.commit()

# Create fake JPGs in incoming-jpg
for name in ["IMG_001.jpg", "IMG_002.jpg", "IMG_003.jpg"]:
    Path(os.path.join(project_dir, "incoming-jpg", name)).write_bytes(b"JFIF" * 100)

from unittest.mock import MagicMock
from app.app_context import AppContext
ctx = AppContext()
ctx.current_project_dir = project_dir
ctx._db_cache = {project_dir: conn}

from app.views.workbench_view import WorkbenchView
w = WorkbenchView(ctx)
w.resize(1920, 1080)
w.show()
w.on_activate()

out = str(Path(__file__).parent / "workbench_func.png")
def snap():
    from PyQt6.QtGui import QPixmap
    pix = w.grab()
    pix.save(out)
    print(f"Saved: {out}")
    app.quit()
QTimer.singleShot(500, snap)
app.exec()
```

Run it:
```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && QT_QPA_PLATFORM=offscreen python docs/shots/capture_workbench_func.py 2>&1 | tail -5
```
Expected: `Saved: .../workbench_func.png`

- [ ] **Step 3: Final commit**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3 && git add docs/shots/capture_workbench_func.py docs/shots/workbench_func.png && git commit -m "docs: add workbench_func.png themed screenshot + capture script"
```

---

## Self-Review

### Spec coverage check

| Feature from spec | Task covering it |
|-------------------|-----------------|
| Auto-poll timer | Task 1 |
| Actual JPG deletion | Task 2 |
| Add-to-group from monitor | Task 3 |
| Remove JPG from group | Task 4 |
| Helicon params UI | Task 5 |
| Sequence naming on compose (hint bump) | Task 6 |
| Free compose (无号合成) | Task 7 |
| Retroactive organize | Task 8 |
| Project settings drawer | Task 9 |
| Add-JPG file picker | Task 10 |
| Open-in-explorer on results | Task 10 |

**Out of scope for this pass (per spec § P3):**
- Compose-preview page (full Helicon GUI with zoom/pan)
- Collab task management UI
- Context menu on specimen sidebar
- Free-compose batch merge

### Placeholder scan
No TBD/TODO/placeholder in code blocks.

### Type consistency
- `Group.jpg_paths: list[str]` — consistent throughout.
- `add_jpgs_to_group(group_index: int, jpg_paths: list[str])` — used in Task 3 and Task 4.
- `selected_jpg_paths() -> list[str]` — used in Task 3 and Task 7.
- `_free_compose_output_name(incoming_dir: str, user_name: str | None) -> str` — used in Task 7 test and handler.
- `stack_single_subprocess(method=str(...), radius=str(...), smoothing=str(...))` — Task 5 and 7 pass params as strings, matching helicon_service signature.
