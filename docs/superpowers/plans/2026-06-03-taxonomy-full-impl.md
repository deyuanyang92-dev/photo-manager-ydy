# Taxonomy View — Full Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every gap between `taxonomy_view.py` and the web 内置分类库 page: add history tracking to `update()`, add rollback UI in the edit dialog, add a full test file for view CRUD/import/export behavior, write the functional spec, and produce a themed 1920×1080 screenshot.

**Architecture:** The service already implements `learn/update/delete/all_records/search`. Missing piece is `history[]` array on `update()` (mirrors `server.js:934-943`) and a "查看/回滚历史" button in `_RecordDialog`. Everything else (CRUD flow, import/export, filter, pagination) is already wired; tests and spec just need to be written.

**Tech Stack:** Python 3.11+, PyQt6, openpyxl (optional for xlsx), pytest, QT_QPA_PLATFORM=offscreen for screenshot capture.

---

## Pre-flight: verify baseline

- [ ] **Step 0: confirm all tests pass before touching anything**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3
python -m pytest tests/test_taxonomy_service.py -q
```
Expected: `52 passed`

---

## Task 1: Write functional spec

**Files:**
- Create: `docs/specs/taxonomy-functional.md`

- [ ] **Step 1: Write the spec**

Create `/mnt/n/claude/photo-platform-ydy-v3/docs/specs/taxonomy-functional.md` with the following content (exact oracle lines included):

```markdown
# 内置分类库 — 完整功能 SPEC

> Oracle: web `app.js` + `server.js:353-730,837-982` + `docs/modules/taxonomy.md`
> Implementation target: `app/views/taxonomy_view.py` + `app/services/taxonomy_service.py`

## 1. 数据层（taxonomy_service.py）

### 1.1 种子库只读（server.js:837,884）
- `taxonomy_seed.json` 永不写入；任何 learn/update/delete 只写 `user_taxonomy.json`。
- `reload()` 强制重新从磁盘加载（用于外部修改后刷新）。

### 1.2 learn（server.js:837 `POST /api/taxonomy/learn`）
- 入参：`class / order / family / species`（4 级拉丁，全部必填）。
- 可选：`classCn / orderCn / familyCn / speciesCn / genus / genusCn`（中文字段，提供则存，不自动填）。
- 行为：4 元组完整 → 按 `class|order|family|species` key 查重。
  - 已存在 → `useCount += 1`，`lastUsedAt = now`，可选字段只在原值为空时更新。
  - 不存在 → 新建条目，`recordId = "user:<16hex>"`，`useCount = 1`，`addedAt = lastUsedAt = now`。
- 4 元组不完整 → 静默返回 `{}`，不写磁盘。
- 返回：upserted record dict。

### 1.3 update（server.js:892 `POST /api/taxonomy/update`）
- 只允许操作 `recordId.startswith("user:")`；种子库不可改。
- 写入前先把旧值追加到 `entry["history"][]`（最多保留 10 条）：
  ```json
  { "at": "<ISO8601>", "before": { "class":…, "classCn":…, "order":…,
    "orderCn":…, "family":…, "familyCn":…, "genus":…, "genusCn":…,
    "species":…, "speciesCn":… } }
  ```
- 写新值（所有 10 个可编辑字段），`lastModifiedAt = now`，保存到磁盘。
- 未找到 recordId → 返回 `None`。

### 1.4 delete（server.js:966 `POST /api/taxonomy/delete`）
- 只删 user 记录（recordId 以 "user:" 开头）。
- 返回 `True` / `False`（是否找到并删除）。

### 1.5 all_records / pagination
- `source_filter`: `None`=全部（user 在前），`"user"`=仅用户，`"seed"`=仅种子。
- 零基分页：`page=0, page_size=50`。
- 返回 `(page_records: list[dict], total_count: int)`。

### 1.6 search / candidates
- `sp_key` ∈ `{taxonGroup, order, family, scientificName}`。
- 空 query → 返回全部候选（受 ancestor 约束）。
- NFKC 归一化 + lowercase，匹配 Latin value 或 cn 字段，按首次命中位置升序排。
- ancestor 约束：知道 class→过滤 order；知道 order→过滤 family；知道 family→过滤 species。
- 结果最多 `max_results`（默认 30）。

---

## 2. 表格页面（taxonomy_view.py）

### 2.1 标题栏（app.js:renderTaxonomyPage ~12060）
- 页面标题「内置分类库」+ 统计「共 N 条」。
- 视图切换：**原始分类 / WoRMS 分类 / 对照视图**（segmented tabs）。
- 图表 toggle（当前为 stub，信息框提示）。

### 2.2 列控制（原始视图专有，app.js ~12100）
- **类群** chips：目 / 科 / 属 / 种（每个可独立开关）。
- **语言** chips：中文 / 拉丁名（每个可独立开关）。
- 纲（taxonGroup）列始终显示（不受类群 chip 控制）。

### 2.3 过滤栏（app.js ~12140）
- 列选择下拉（全部列 / 纲中 / 纲拉 / 目中 / 目拉 / 科中 / 科拉 / 属中 / 属拉 / 种中 / 种拉）。
- 搜索框 + 搜索按钮 + 清除按钮。
- 过滤激活时显示「已筛选 N 条」标签。
- 搜索为客户端过滤（桌面 GUI，无网络延迟，全量加载后本地筛）。

### 2.4 操作栏（app.js ~12160）
- **+ 新增条目**（仅原始视图）→ 弹出新增对话框。
- 已选 N 条 / 已选择全部筛选结果（N 条）。
- **全选筛选结果** / **取消选择**。
- **WoRMS 更新所选**（当前为 stub，提示前往 WoRMS 页）。
- **WoRMS 更新筛选结果**（stub）。
- **导出 Excel** / **导出 CSV**（导出当前视图全部记录，非仅当前页）。
- **导入 Excel/CSV**（弹出文件选择器，解析后批量 learn）。

### 2.5 表格（app.js ~12200）
- 列：☑ | # | 动态数据列 | 来源 | 操作。
- 数据列按 2.2 开关动态调整。
- ☑ 列：checkbox，全选/取消全选。
- # 列：显示全局行号（当前页偏移 + 行内索引 + 1）。
- 来源列：用户记录显示「用户」（绿）；种子记录显示「种子」（灰）。
- 操作列：每行内嵌「编辑」按钮；用户记录额外「删除」按钮。
- 用户记录行底色略微高亮（浅青色背景）。
- 双击行 → 若为用户记录则弹编辑对话框；种子记录弹只读提示。

### 2.6 编辑/新增对话框（app.js openTaxonomyTableModal）
- 字段：class / order / family / species（必填）+ classCn / orderCn / familyCn / speciesCn / genus / genusCn（可选）。
- 必填项未填时阻止提交并聚焦该字段。
- **编辑时**：若记录含 `history[]` 则显示「查看历史」按钮。
- 「查看历史」→ 弹历史列表对话框，每条显示 `at` 时间 + 变更前的 10 字段值；「回滚」按钮把该快照写回表单（不立即保存）。

### 2.7 删除确认
- 弹 QMessageBox 确认，显示物种名（种拉丁 + 纲）。
- 只对用户记录（种子不可删）。

### 2.8 导入（server.js:777 `POST /api/taxonomy/import`）
- 支持 `.xlsx` / `.xls` / `.csv`。
- 首行为表头，大小写不敏感，支持中英文列名：
  `class/纲 · order/目 · family/科 · species/种 · classCn/纲中文 · orderCn/目中文 ·
   familyCn/科中文 · speciesCn/种中文 · genus/属 · genusCn/属中文`。
- 逐行调 `learn()`；4 元组不完整则跳过（skipped 计数）。
- 导入完成后弹「成功 N 条，跳过 M 条」并刷新表格。

### 2.9 导出（server.js:410 `exportTaxonomyRows`）
- 导出当前视图全部记录（非分页，page_size=999999）。
- CSV：UTF-8 BOM，逗号分隔，首行表头（当前可见列标签 + 来源）。
- XLSX：openpyxl，sheet 名「分类库」，首行表头，每行数据。
- 导出完成后弹确认框（成功 N 条 + 保存路径）。

### 2.10 分页
- 默认每页 50 条。
- 上一页 / 下一页 / 跳到第 N 页（QSpinBox）。
- 页脚显示「第 P / T 页（共 K 条）」+ 「种子库 S 条 | 用户 U 条」。

### 2.11 自动学习（auto-learn）
- 在 `TaxonomyInputPanel`（taxonomy_input.py）中：用户在工作台录入标本分类，输入框 blur/编辑完成时若 4 元组完整则调 `svc.learn()`。
- 中文字段 (`*Cn`) 永不被自动填充。
- 见 `app/widgets/taxonomy_input.py _on_editing_finished` / `_commit_candidate`。

---

## 3. 硬规则（永不例外）

| 规则 | Oracle |
|------|--------|
| seed 只读，永不写入 | server.js:884 `atomicWriteJson(TAXONOMY_USER_PATH, ...)` |
| 中文字段不自动填充 | taxonomy.md 最后一条 |
| update 保存 history（≤10条） | server.js:934-943 |
| 导入 4 元组不完整 → 跳过 | server.js:802-810 |
| 删除只对 user: 记录 | server.js:966-980 |
```

- [ ] **Step 2: Commit**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3
git add docs/specs/taxonomy-functional.md
git commit -m "docs: add taxonomy-functional.md spec with full web oracle references"
```

---

## Task 2: Add history tracking to `taxonomy_service.update()`

**Files:**
- Modify: `app/services/taxonomy_service.py` (method `update`, ~line 386)
- Modify: `tests/test_taxonomy_service.py` (add `TestHistory` class)

- [ ] **Step 1: Write failing tests for history**

Add the following class to `tests/test_taxonomy_service.py` (at the end, before the widget smoke tests section):

```python
# ── History tracking ──────────────────────────────────────────────────────────

class TestHistory:
    def test_update_stores_history_entry(self, svc):
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
            "classCn": "多毛纲",
        })
        records, _ = svc.all_records(source_filter="user")
        rec_id = records[0]["recordId"]
        svc.update(rec_id, {"familyCn": "多鳞虫科_v2"})
        records2, _ = svc.all_records(source_filter="user")
        hist = records2[0].get("history", [])
        assert len(hist) == 1
        assert "at" in hist[0]
        assert "before" in hist[0]
        assert hist[0]["before"]["familyCn"] == "多鳞虫科"

    def test_update_history_max_10(self, svc):
        """History list never exceeds 10 entries."""
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        records, _ = svc.all_records(source_filter="user")
        rec_id = records[0]["recordId"]
        for i in range(15):
            svc.update(rec_id, {"orderCn": f"叶须虫目_v{i}"})
        records2, _ = svc.all_records(source_filter="user")
        hist = records2[0].get("history", [])
        assert len(hist) <= 10

    def test_update_history_persists_to_disk(self, svc, tmp_dirs):
        _, user_p = tmp_dirs
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        records, _ = svc.all_records(source_filter="user")
        rec_id = records[0]["recordId"]
        svc.update(rec_id, {"orderCn": "叶须虫目_new"})
        data = json.loads(user_p.read_text())
        hist = data[0].get("history", [])
        assert len(hist) == 1
        assert hist[0]["before"]["orderCn"] == ""

    def test_update_history_before_has_all_fields(self, svc):
        """history[].before must contain all 10 editable fields."""
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        records, _ = svc.all_records(source_filter="user")
        rec_id = records[0]["recordId"]
        svc.update(rec_id, {"classCn": "多毛纲"})
        records2, _ = svc.all_records(source_filter="user")
        before = records2[0]["history"][0]["before"]
        expected_keys = {
            "class", "classCn", "order", "orderCn",
            "family", "familyCn", "genus", "genusCn",
            "species", "speciesCn",
        }
        assert set(before.keys()) == expected_keys
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3
python -m pytest tests/test_taxonomy_service.py::TestHistory -v
```
Expected: 4 FAILED (AttributeError or AssertionError — history key missing)

- [ ] **Step 3: Implement history in `taxonomy_service.update()`**

In `/mnt/n/claude/photo-platform-ydy-v3/app/services/taxonomy_service.py`, replace the body of the `update()` method (lines ~405-424) with:

```python
    def update(self, record_id: str, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Update an existing user record by recordId.

        Saves a history snapshot before writing (mirrors server.js:934-943).
        History is capped at 10 entries (oldest dropped when over limit).
        """
        self._ensure_loaded()

        entry = next(
            (e for e in self._user if e.get("recordId") == record_id), None
        )
        if entry is None:
            return None  # not found or is a seed record

        # ── Record history (mirrors server.js:934-943) ─────────────────
        before = {
            "class":     entry.get("class", ""),
            "classCn":   entry.get("classCn", ""),
            "order":     entry.get("order", ""),
            "orderCn":   entry.get("orderCn", ""),
            "family":    entry.get("family", ""),
            "familyCn":  entry.get("familyCn", ""),
            "genus":     entry.get("genus", ""),
            "genusCn":   entry.get("genusCn", ""),
            "species":   entry.get("species", ""),
            "speciesCn": entry.get("speciesCn", ""),
        }
        history = entry.setdefault("history", [])
        history.append({"at": _now_iso(), "before": before})
        if len(history) > 10:
            history.pop(0)

        # ── Apply updates ──────────────────────────────────────────────
        allowed = {
            "class", "order", "family", "species",
            "classCn", "orderCn", "familyCn", "speciesCn",
            "genus", "genusCn",
        }
        for k, v in updates.items():
            if k in allowed:
                entry[k] = v

        entry["lastModifiedAt"] = _now_iso()
        self._save_user()
        return dict(entry)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3
python -m pytest tests/test_taxonomy_service.py -v -q
```
Expected: 56 passed (52 existing + 4 new)

- [ ] **Step 5: Commit**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3
git add app/services/taxonomy_service.py tests/test_taxonomy_service.py
git commit -m "feat(taxonomy): track edit history in update() — mirrors server.js:934-943"
```

---

## Task 3: Add history rollback UI to `_RecordDialog`

**Files:**
- Modify: `app/views/taxonomy_view.py` (classes `_RecordDialog` and `_HistoryDialog`)

- [ ] **Step 1: Write failing smoke test**

Add to the `TestTaxonomyViewSmoke` class in `tests/test_taxonomy_service.py`:

```python
    def test_record_dialog_history_button_visible_when_history_present(
        self, svc, app_instance
    ):
        """_RecordDialog shows 'history' button when record has history."""
        from app.views.taxonomy_view import _RecordDialog
        # Learn a record then update it to create history
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        records, _ = svc.all_records(source_filter="user")
        rec_id = records[0]["recordId"]
        svc.update(rec_id, {"classCn": "多毛纲"})
        records2, _ = svc.all_records(source_filter="user")
        rec = records2[0]
        assert "history" in rec
        dlg = _RecordDialog(record=rec)
        # history button must exist
        assert hasattr(dlg, "_btn_history")
        assert dlg._btn_history.isVisible()

    def test_record_dialog_no_history_button_when_no_history(
        self, svc, app_instance
    ):
        """_RecordDialog hides history button when record has no history."""
        from app.views.taxonomy_view import _RecordDialog
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        records, _ = svc.all_records(source_filter="user")
        rec = records[0]
        assert not rec.get("history")
        dlg = _RecordDialog(record=rec)
        assert hasattr(dlg, "_btn_history")
        assert not dlg._btn_history.isVisible()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3
python -m pytest tests/test_taxonomy_service.py::TestTaxonomyViewSmoke -v
```
Expected: 2 FAILED (AttributeError `_btn_history`)

- [ ] **Step 3: Add `_HistoryDialog` and update `_RecordDialog`**

In `/mnt/n/claude/photo-platform-ydy-v3/app/views/taxonomy_view.py`, add the `_HistoryDialog` class right after `_RecordDialog` (after line ~399), and modify `_RecordDialog._build_ui` to include a history button.

Replace the entire `_RecordDialog` class (lines ~346-399) and add `_HistoryDialog` immediately after:

```python
class _RecordDialog(QDialog):
    """Form dialog for adding or editing a user taxonomy record.

    Mirrors openTaxonomyTableModal() in app.js.
    Shows a '查看历史' button when the record has a history[] list.
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        record: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑分类条目" if record else "新增分类条目")
        self.setMinimumWidth(420)
        self._record = record or {}
        self._inputs: dict[str, QLineEdit] = {}
        self._btn_history: QPushButton = QPushButton("查看历史")
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 12)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        for key, label_text, required in _DIALOG_FIELDS:
            inp = QLineEdit()
            inp.setText(self._record.get(key, ""))
            if required:
                inp.setPlaceholderText("必填")
            form.addRow(label_text, inp)
            self._inputs[key] = inp

        layout.addLayout(form)

        # History button — visible only when record has history entries
        history = self._record.get("history", [])
        self._btn_history.setObjectName("Outline")
        self._btn_history.setVisible(bool(history))
        self._btn_history.clicked.connect(self._show_history)
        layout.addWidget(self._btn_history)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        for key, label_text, required in _DIALOG_FIELDS:
            if required and not self._inputs[key].text().strip():
                QMessageBox.warning(self, "必填项", f"「{label_text}」不能为空")
                self._inputs[key].setFocus()
                return
        self.accept()

    def _show_history(self) -> None:
        history = self._record.get("history", [])
        if not history:
            return
        dlg = _HistoryDialog(self, history=history)
        restored = dlg.exec_and_get()
        if restored is not None:
            # Apply restored snapshot to the form inputs
            for k, v in restored.items():
                inp = self._inputs.get(k)
                if inp is not None:
                    inp.setText(str(v))

    def get_record(self) -> dict[str, Any]:
        return {k: inp.text().strip() for k, inp in self._inputs.items()}


class _HistoryDialog(QDialog):
    """Shows history entries for a user record and allows 1-level rollback.

    Each entry in history[] has: { "at": ISO8601, "before": {10 fields} }
    Selecting an entry and clicking "回滚到此版本" fills the parent form
    with the snapshot values (the parent dialog still needs OK to persist).
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        history: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑历史")
        self.setMinimumWidth(560)
        self.setMinimumHeight(320)
        self._history = list(reversed(history or []))  # newest first
        self._selected_before: Optional[dict[str, Any]] = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        info = QLabel("选择历史版本后点击「回滚」，将把该快照填入编辑框（需再点确定保存）。")
        info.setWordWrap(True)
        info.setStyleSheet("color:#87a2a1; font-size:11px;")
        layout.addWidget(info)

        from PyQt6.QtWidgets import QListWidget, QListWidgetItem
        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        for entry in self._history:
            at = entry.get("at", "")
            before = entry.get("before", {})
            species = before.get("species", "")
            family  = before.get("family", "")
            label   = f"{at[:19].replace('T', ' ')}  →  {species} ({family})"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, before)
            self._list.addItem(item)
        layout.addWidget(self._list, 1)

        buttons = QDialogButtonBox()
        btn_rollback = buttons.addButton("回滚到此版本", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_cancel   = buttons.addButton("取消",         QDialogButtonBox.ButtonRole.RejectRole)
        btn_rollback.clicked.connect(self._on_rollback)
        btn_cancel.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _on_rollback(self) -> None:
        item = self._list.currentItem()
        if item is None:
            QMessageBox.information(self, "提示", "请先选择一条历史记录")
            return
        self._selected_before = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def exec_and_get(self) -> Optional[dict[str, Any]]:
        """Run dialog modally; return the snapshot dict if user confirmed, else None."""
        if self.exec() == QDialog.DialogCode.Accepted:
            return self._selected_before
        return None
```

You also need to add `QListWidget, QListWidgetItem` to the top-level imports (they are already available from `PyQt6.QtWidgets`; no extra import needed since they are imported inside the method).

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3
python -m pytest tests/test_taxonomy_service.py -v -q
```
Expected: 58 passed

- [ ] **Step 5: Syntax check**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3
python -c "import py_compile; py_compile.compile('app/views/taxonomy_view.py', doraise=True); print('OK')"
```
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3
git add app/views/taxonomy_view.py tests/test_taxonomy_service.py
git commit -m "feat(taxonomy): add history rollback dialog to _RecordDialog"
```

---

## Task 4: Add dedicated `tests/test_taxonomy_view.py`

**Files:**
- Create: `tests/test_taxonomy_view.py`

This file tests the VIEW behaviors (CRUD flow, import, export) that are not covered in `test_taxonomy_service.py`.

- [ ] **Step 1: Create the test file**

Create `/mnt/n/claude/photo-platform-ydy-v3/tests/test_taxonomy_view.py`:

```python
"""test_taxonomy_view.py — Behavioral tests for TaxonomyView and its helpers.

Covers:
  - _RecordDialog: field values, required-field validation, history button visibility
  - _HistoryDialog: list population, rollback returns snapshot
  - _TaxonTableModel: records / checked state / column rebuild
  - TaxonomyView._import_rows: column alias mapping, skip incomplete rows, count
  - TaxonomyView._export_csv: CSV output format
  - TaxonomyView._export_xlsx: xlsx output (skipped if openpyxl absent)
  - TaxonomyView on_activate loads records into table
"""
from __future__ import annotations

import csv
import io
import json
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


# ── QApplication fixture ──────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


# ── Service + seed fixture ────────────────────────────────────────────────────

SEED = [
    {
        "class": "Polychaeta",   "order": "Phyllodocida",
        "family": "Polynoidae",  "species": "Halosydna brevisetosa",
        "classCn": "多毛纲",     "orderCn": "叶须虫目",
        "familyCn": "多鳞虫科",  "genus": "Halosydna",
        "genusCn": "海鳞虫属",   "speciesCn": "短毛海鳞虫",
    },
    {
        "class": "Malacostraca", "order": "Decapoda",
        "family": "Portunidae",  "species": "Portunus trituberculatus",
        "classCn": "软甲纲",     "orderCn": "十足目",
        "familyCn": "梭子蟹科",  "genus": "Portunus",
        "genusCn": "梭子蟹属",   "speciesCn": "三疣梭子蟹",
    },
]


@pytest.fixture
def tmp_svc():
    """Yield a TaxonomyService backed by a temp dir."""
    import shutil
    from app.services.taxonomy_service import TaxonomyService
    d = Path(tempfile.mkdtemp())
    seed_p = d / "taxonomy_seed.json"
    user_p = d / "user_taxonomy.json"
    seed_p.write_text(json.dumps(SEED), encoding="utf-8")
    svc = TaxonomyService(seed_p, user_p)
    try:
        yield svc
    finally:
        shutil.rmtree(d)


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.current_project_dir = None
    ctx.settings = MagicMock()
    ctx.settings.last_nav_index = 0
    return ctx


@pytest.fixture
def view(qapp, mock_ctx, tmp_svc):
    from app.views.taxonomy_view import TaxonomyView
    v = TaxonomyView(mock_ctx)
    v._svc = tmp_svc
    return v


# ── _RecordDialog ─────────────────────────────────────────────────────────────

class TestRecordDialog:
    def test_dialog_constructs_no_record(self, qapp):
        from app.views.taxonomy_view import _RecordDialog
        dlg = _RecordDialog()
        assert dlg.windowTitle() == "新增分类条目"

    def test_dialog_constructs_with_record(self, qapp):
        from app.views.taxonomy_view import _RecordDialog
        rec = {
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
            "recordId": "user:abc123",
        }
        dlg = _RecordDialog(record=rec)
        assert dlg.windowTitle() == "编辑分类条目"

    def test_dialog_prepopulates_fields(self, qapp):
        from app.views.taxonomy_view import _RecordDialog
        rec = {"class": "Polychaeta", "order": "Phyllodocida",
               "family": "Polynoidae", "species": "Halosydna brevisetosa"}
        dlg = _RecordDialog(record=rec)
        assert dlg._inputs["class"].text() == "Polychaeta"
        assert dlg._inputs["order"].text() == "Phyllodocida"

    def test_get_record_returns_all_fields(self, qapp):
        from app.views.taxonomy_view import _RecordDialog, _DIALOG_FIELDS
        dlg = _RecordDialog()
        result = dlg.get_record()
        assert set(result.keys()) == {k for k, _, _ in _DIALOG_FIELDS}

    def test_history_button_hidden_when_no_history(self, qapp):
        from app.views.taxonomy_view import _RecordDialog
        rec = {"class": "Polychaeta", "order": "Phyllodocida",
               "family": "Polynoidae", "species": "X", "recordId": "user:1"}
        dlg = _RecordDialog(record=rec)
        assert not dlg._btn_history.isVisible()

    def test_history_button_visible_when_history_present(self, qapp):
        from app.views.taxonomy_view import _RecordDialog
        rec = {
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "X", "recordId": "user:1",
            "history": [{"at": "2026-01-01T00:00:00Z",
                         "before": {"class": "Old", "classCn": "", "order": "O",
                                    "orderCn": "", "family": "F", "familyCn": "",
                                    "genus": "", "genusCn": "", "species": "X",
                                    "speciesCn": ""}}],
        }
        dlg = _RecordDialog(record=rec)
        assert dlg._btn_history.isVisible()


# ── _HistoryDialog ────────────────────────────────────────────────────────────

class TestHistoryDialog:
    def test_dialog_constructs_with_history(self, qapp):
        from app.views.taxonomy_view import _HistoryDialog
        hist = [
            {"at": "2026-01-02T10:00:00Z",
             "before": {"class": "Poly", "classCn": "", "order": "Phyllo",
                        "orderCn": "", "family": "Poly-fam", "familyCn": "",
                        "genus": "", "genusCn": "", "species": "X sp",
                        "speciesCn": ""}},
        ]
        dlg = _HistoryDialog(history=hist)
        assert dlg._list.count() == 1

    def test_dialog_shows_newest_first(self, qapp):
        from app.views.taxonomy_view import _HistoryDialog
        hist = [
            {"at": "2026-01-01T00:00:00Z",
             "before": {"class": "A", "classCn": "", "order": "B",
                        "orderCn": "", "family": "C", "familyCn": "",
                        "genus": "", "genusCn": "", "species": "D sp",
                        "speciesCn": ""}},
            {"at": "2026-01-03T00:00:00Z",
             "before": {"class": "A", "classCn": "", "order": "B",
                        "orderCn": "", "family": "C", "familyCn": "",
                        "genus": "", "genusCn": "", "species": "D sp",
                        "speciesCn": ""}},
        ]
        dlg = _HistoryDialog(history=hist)
        # Newest (2026-01-03) should be first
        first_text = dlg._list.item(0).text()
        assert "2026-01-03" in first_text


# ── _TaxonTableModel ──────────────────────────────────────────────────────────

class TestTaxonTableModel:
    def test_set_records_updates_row_count(self, qapp):
        from app.views.taxonomy_view import _TaxonTableModel
        m = _TaxonTableModel()
        m.set_records(SEED)
        assert m.rowCount() == len(SEED)

    def test_row_number_column_shows_offset_plus_one(self, qapp):
        from PyQt6.QtCore import Qt
        from app.views.taxonomy_view import _TaxonTableModel, _COL_NUM
        m = _TaxonTableModel()
        m.set_records(SEED, page_offset=50)
        val = m.data(m.index(0, _COL_NUM), Qt.ItemDataRole.DisplayRole)
        assert val == "51"

    def test_checked_state_toggle(self, qapp):
        from PyQt6.QtCore import Qt
        from app.views.taxonomy_view import _TaxonTableModel, _COL_CHECK
        m = _TaxonTableModel()
        recs = [dict(r, recordId=f"user:test{i}") for i, r in enumerate(SEED)]
        m.set_records(recs)
        idx = m.index(0, _COL_CHECK)
        m.setData(idx, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        assert "user:test0" in m.checked_ids()
        m.setData(idx, Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
        assert "user:test0" not in m.checked_ids()

    def test_clear_checked(self, qapp):
        from PyQt6.QtCore import Qt
        from app.views.taxonomy_view import _TaxonTableModel, _COL_CHECK
        m = _TaxonTableModel()
        recs = [dict(r, recordId=f"user:test{i}") for i, r in enumerate(SEED)]
        m.set_records(recs)
        m.set_all_page_checked(True)
        assert len(m.checked_ids()) == len(SEED)
        m.clear_checked()
        assert len(m.checked_ids()) == 0

    def test_source_column_shows_seed_or_user(self, qapp):
        from PyQt6.QtCore import Qt
        from app.views.taxonomy_view import _TaxonTableModel, _COL_DATA_START
        m = _TaxonTableModel()
        recs = [
            {**SEED[0], "recordId": "seed:0"},
            {**SEED[1], "recordId": "user:abc"},
        ]
        m.set_records(recs)
        n_data = len(m.columns())
        src_col = _COL_DATA_START + n_data
        seed_val = m.data(m.index(0, src_col), Qt.ItemDataRole.DisplayRole)
        user_val = m.data(m.index(1, src_col), Qt.ItemDataRole.DisplayRole)
        assert seed_val == "种子"
        assert user_val == "用户"


# ── _import_rows ──────────────────────────────────────────────────────────────

class TestImportRows:
    @pytest.fixture
    def view_with_svc(self, qapp, mock_ctx, tmp_svc):
        from app.views.taxonomy_view import TaxonomyView
        v = TaxonomyView(mock_ctx)
        v._svc = tmp_svc
        return v

    def test_import_english_headers(self, view_with_svc):
        header = ["class", "order", "family", "species"]
        rows = [
            ["Polychaeta", "Phyllodocida", "Polynoidae", "Halosydna brevisetosa"],
        ]
        imported, skipped = view_with_svc._import_rows(header, rows)
        assert imported == 1
        assert skipped == 0
        assert view_with_svc._svc.user_count() == 1

    def test_import_chinese_headers(self, view_with_svc):
        header = ["纲", "目", "科", "种"]
        rows = [
            ["Polychaeta", "Phyllodocida", "Polynoidae", "Halosydna brevisetosa"],
        ]
        imported, skipped = view_with_svc._import_rows(header, rows)
        assert imported == 1
        assert skipped == 0

    def test_import_skips_incomplete_rows(self, view_with_svc):
        header = ["class", "order", "family", "species"]
        rows = [
            ["Polychaeta", "Phyllodocida", "", "Halosydna brevisetosa"],  # missing family
            ["Polychaeta", "Phyllodocida", "Polynoidae", "Halosydna brevisetosa"],
        ]
        imported, skipped = view_with_svc._import_rows(header, rows)
        assert imported == 1
        assert skipped == 1

    def test_import_optional_cn_fields(self, view_with_svc):
        header = ["class", "order", "family", "species", "classCn", "familyCn"]
        rows = [
            ["Polychaeta", "Phyllodocida", "Polynoidae",
             "Halosydna brevisetosa", "多毛纲", "多鳞虫科"],
        ]
        view_with_svc._import_rows(header, rows)
        recs, _ = view_with_svc._svc.all_records(source_filter="user")
        assert recs[0].get("classCn") == "多毛纲"

    def test_import_multiple_rows(self, view_with_svc):
        header = ["class", "order", "family", "species"]
        rows = [
            ["Polychaeta", "Phyllodocida", "Polynoidae", "Halosydna brevisetosa"],
            ["Malacostraca", "Decapoda", "Portunidae", "Portunus trituberculatus"],
            ["Incomplete", "", "Fam", "Sp"],  # missing order → skip
        ]
        imported, skipped = view_with_svc._import_rows(header, rows)
        assert imported == 2
        assert skipped == 1


# ── _export_csv ───────────────────────────────────────────────────────────────

class TestExportCsv:
    def test_csv_written_with_header(self, view, tmp_path, qapp):
        out = tmp_path / "test_export.csv"
        # Load some records first
        view._svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        view.on_activate()

        # Patch QFileDialog to return our temp path
        import unittest.mock as mock
        with mock.patch(
            "app.views.taxonomy_view.QFileDialog.getSaveFileName",
            return_value=(str(out), "CSV 文件 (*.csv)"),
        ):
            view._export_csv(
                view._svc.all_records(page=0, page_size=99999)[0]
            )

        assert out.exists()
        content = out.read_text(encoding="utf-8-sig")
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        assert len(rows) >= 2   # header + at least 1 data row
        # Last column of header should be "来源"
        assert rows[0][-1] == "来源"

    def test_csv_user_record_shows_correct_source(self, view, tmp_path, qapp):
        out = tmp_path / "test_export2.csv"
        view._svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        recs, _ = view._svc.all_records(source_filter="user")

        import unittest.mock as mock
        with mock.patch(
            "app.views.taxonomy_view.QFileDialog.getSaveFileName",
            return_value=(str(out), ""),
        ):
            view._export_csv(recs)

        content = out.read_text(encoding="utf-8-sig")
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        # First data row should be "用户"
        assert rows[1][-1] == "用户"


# ── TaxonomyView on_activate ──────────────────────────────────────────────────

class TestViewOnActivate:
    def test_on_activate_loads_records(self, view, qapp):
        view.on_activate()
        assert view._model.rowCount() > 0

    def test_on_activate_updates_stats_label(self, view, qapp):
        view.on_activate()
        text = view._stats_label.text()
        assert "条" in text
        count = view._svc.seed_count() + view._svc.user_count()
        assert str(count) in text

    def test_on_activate_footer_shows_seed_and_user(self, view, qapp):
        view.on_activate()
        footer = view._footer_label.text()
        assert "种子库" in footer
        assert "用户" in footer

    def test_pagination_initial_page_is_1(self, view, qapp):
        view.on_activate()
        assert view._page == 1

    def test_total_equals_seed_count_initially(self, view, qapp):
        view.on_activate()
        assert view._total == len(SEED)
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3
python -m pytest tests/test_taxonomy_view.py -v
```
Expected: all pass (≥35 tests). Any FAIL is a real bug to fix before continuing.

- [ ] **Step 3: Commit**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3
git add tests/test_taxonomy_view.py
git commit -m "test(taxonomy): add dedicated test_taxonomy_view.py covering CRUD/import/export/history"
```

---

## Task 5: Screenshot `docs/shots/taxonomy_func.png`

**Files:**
- Create: `docs/shots/capture_taxonomy_func.py`
- Create: `docs/shots/taxonomy_func.png`

The existing `capture_taxonomy.py` produces `page_taxonomy.png` at 1440×900. The new one:
- Uses 1920×1080
- Shows a user record with history (for the history button to be visible)
- Is named `taxonomy_func.png`

- [ ] **Step 1: Create the capture script**

Create `/mnt/n/claude/photo-platform-ydy-v3/docs/shots/capture_taxonomy_func.py`:

```python
"""capture_taxonomy_func.py — 1920x1080 screenshot of TaxonomyView for functional review.

Usage:
    QT_QPA_PLATFORM=offscreen python3 docs/shots/capture_taxonomy_func.py
Output:
    docs/shots/taxonomy_func.png
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from PyQt6.QtWidgets import QApplication, QMainWindow  # noqa: E402

from app.app_context import AppContext  # noqa: E402
from app.config.theme import build_theme_qss_file, load_fonts  # noqa: E402
from app.views.taxonomy_view import TaxonomyView  # noqa: E402
from app.services.taxonomy_service import TaxonomyService  # noqa: E402


_SEED_DATA = [
    {
        "class": "Polychaeta",   "order": "Phyllodocida",
        "family": "Polynoidae",  "species": "Halosydna brevisetosa",
        "classCn": "多毛纲",     "orderCn": "叶须虫目",
        "familyCn": "多鳞虫科",  "genus": "Halosydna",
        "genusCn": "海鳞虫属",   "speciesCn": "短毛海鳞虫",
    },
    {
        "class": "Polychaeta",   "order": "Phyllodocida",
        "family": "Aphroditidae","species": "Aphrodita aculeata",
        "classCn": "多毛纲",     "orderCn": "叶须虫目",
        "familyCn": "鳞沙蚕科",  "genus": "Aphrodita",
        "genusCn": "鳞沙蚕属",   "speciesCn": "棘鳞沙蚕",
    },
    {
        "class": "Malacostraca", "order": "Decapoda",
        "family": "Portunidae",  "species": "Portunus trituberculatus",
        "classCn": "软甲纲",     "orderCn": "十足目",
        "familyCn": "梭子蟹科",  "genus": "Portunus",
        "genusCn": "梭子蟹属",   "speciesCn": "三疣梭子蟹",
    },
    {
        "class": "Gastropoda",   "order": "Neogastropoda",
        "family": "Conidae",     "species": "Conus textile",
        "classCn": "腹足纲",     "orderCn": "新腹足目",
        "familyCn": "芋螺科",    "genus": "Conus",
        "genusCn": "芋螺属",     "speciesCn": "织锦芋螺",
    },
    {
        "class": "Bivalvia",     "order": "Mytilida",
        "family": "Mytilidae",   "species": "Mytilus edulis",
        "classCn": "双壳纲",     "orderCn": "贻贝目",
        "familyCn": "贻贝科",    "genus": "Mytilus",
        "genusCn": "贻贝属",     "speciesCn": "紫贻贝",
    },
    {
        "class": "Echinoidea",   "order": "Diadematoida",
        "family": "Diadematidae","species": "Diadema setosum",
        "classCn": "海胆纲",     "orderCn": "冠海胆目",
        "familyCn": "冠海胆科",  "genus": "Diadema",
        "genusCn": "冠海胆属",   "speciesCn": "刺冠海胆",
    },
]


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    load_fonts(app)
    qss_file = build_theme_qss_file()
    app.setStyleSheet(qss_file.read_text(encoding="utf-8"))

    tmp = Path(tempfile.mkdtemp(prefix="taxon-func-shot-"))
    seed_p = tmp / "taxonomy_seed.json"
    user_p = tmp / "user_taxonomy.json"
    seed_p.write_text(json.dumps(_SEED_DATA), encoding="utf-8")
    user_p.write_text("[]", encoding="utf-8")

    ctx = MagicMock(spec=AppContext)
    ctx.current_project_dir = None
    ctx.settings = MagicMock()
    ctx.settings.last_nav_index = 0

    view = TaxonomyView(ctx)
    svc = TaxonomyService(seed_p, user_p)
    view._svc = svc

    # Add user records with history (to show history button is wired)
    svc.learn({
        "class": "Polychaeta",  "order": "Phyllodocida",
        "family": "Polynoidae", "species": "Harmothoe imbricata",
        "classCn": "多毛纲",    "orderCn": "叶须虫目",
        "familyCn": "多鳞虫科", "genus": "Harmothoe",
        "genusCn": "叶须虫属",  "speciesCn": "覆瓦叶须虫",
    })
    # Update to create a history entry
    recs, _ = svc.all_records(source_filter="user")
    if recs:
        svc.update(recs[0]["recordId"], {"orderCn": "叶须虫目（已验证）"})

    svc.learn({
        "class": "Cephalopoda", "order": "Octopoda",
        "family": "Octopodidae","species": "Octopus vulgaris",
        "classCn": "头足纲",    "orderCn": "八腕目",
        "familyCn": "章鱼科",   "genus": "Octopus",
        "genusCn": "章鱼属",    "speciesCn": "普通章鱼",
    })

    win = QMainWindow()
    win.setWindowTitle("内置分类库 — 功能截图 1920×1080")
    win.setCentralWidget(view)
    win.resize(1920, 1080)
    win.show()

    view.on_activate()

    for _ in range(15):
        app.processEvents()

    out = Path(__file__).resolve().parent / "taxonomy_func.png"
    pix = win.grab()
    pix.save(str(out))
    size = out.stat().st_size if out.exists() else 0
    print(f"saved: {out}  ({pix.width()}x{pix.height()}, {size} bytes)")
    return 0 if size > 5000 else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run the capture script**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3
QT_QPA_PLATFORM=offscreen python3 docs/shots/capture_taxonomy_func.py
```
Expected output: `saved: …/taxonomy_func.png  (1920x1080, NNNN bytes)` — size > 5000 bytes.

- [ ] **Step 3: Verify screenshot file exists and is non-trivial**

```bash
ls -lh /mnt/n/claude/photo-platform-ydy-v3/docs/shots/taxonomy_func.png
```
Expected: file ≥ 50 KB (full render)

- [ ] **Step 4: Commit**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3
git add docs/shots/capture_taxonomy_func.py docs/shots/taxonomy_func.png
git commit -m "docs(shots): add taxonomy_func.png 1920x1080 themed screenshot"
```

---

## Task 6: Full test suite regression check

- [ ] **Step 1: Run all tests**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3
python -m pytest -q 2>&1 | tail -5
```
Expected: `≥ 890 passed, 4 skipped` (baseline 851 + ~40 new)

- [ ] **Step 2: If any failures, investigate and fix**

Check which tests fail:
```bash
python -m pytest --tb=short -q 2>&1 | grep FAILED
```
Fix any regressions before the final commit.

- [ ] **Step 3: Final commit if needed**

```bash
cd /mnt/n/claude/photo-platform-ydy-v3
git add -p   # only relevant files
git commit -m "fix(taxonomy): regression fixes from full test run"
```

---

## Self-Review Checklist

| Spec requirement | Task covering it |
|-----------------|-----------------|
| Spec doc with oracle line numbers | Task 1 |
| history[] saved on update() | Task 2 |
| history max 10 entries | Task 2 |
| history persists to disk | Task 2 |
| history rollback dialog in edit UI | Task 3 |
| Tests for CRUD flow | Task 4 |
| Tests for import_rows (all alias types) | Task 4 |
| Tests for export_csv (format, source label) | Task 4 |
| Tests for table model (row count, source col, check) | Task 4 |
| Tests for view on_activate (stats, total, pagination) | Task 4 |
| Screenshot 1920×1080 with user records + history | Task 5 |
| Full suite regression | Task 6 |

**Already implemented before this plan (no tasks needed):**
- CRUD dialogs wired to service (learn/update/delete): taxonomy_view.py:1251-1291
- Export CSV/XLSX: taxonomy_view.py:1319-1358
- Import Excel/CSV: taxonomy_view.py:1362-1474
- Column visibility chips: taxonomy_view.py:688-717
- View switch (original/worms/compare): taxonomy_view.py:1018-1050
- Pagination: taxonomy_view.py:1109-1127
- Filter/search: taxonomy_view.py:1086-1105
- Selection: taxonomy_view.py:1211-1233
