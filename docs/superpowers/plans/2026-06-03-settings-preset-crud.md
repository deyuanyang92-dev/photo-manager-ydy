# settings_view.py 完整功能实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `app/views/settings_view.py` 补全合成参数预设 CRUD（命名预设增/删/改/选）功能，并生成截图验证。

**Architecture:** 在现有 Helicon tab 的 `QGroupBox("合成参数预设")` 区域内，用 `QListWidget` 展示命名预设列表，用 `QLineEdit` 输入预设名称，用三个按钮实现 upsert/apply/delete。预设持久化使用 QSettings 的 `helicon/presets_json` 键（JSON 字符串），与 web `helicon_presets.json` 语义等价。单套当前参数继续用 `helicon/method` 等四个独立键（向后兼容）。

**Tech Stack:** PyQt6, QSettings, JSON, pytest, offscreen QPA

---

## 文件变动

| 操作 | 文件 |
|------|------|
| 修改 | `app/views/settings_view.py` |
| 修改 | `tests/test_settings_view.py` |
| 创建（截图） | `docs/shots/settings_func.png` |

---

### Task 1: 添加 `_K_HELICON_PRESETS_JSON` 常量 + 重构预设 QGroupBox UI

**Files:**
- Modify: `app/views/settings_view.py:60-76` （常量块）
- Modify: `app/views/settings_view.py:377-414` （preset_box 构建段）

- [ ] **Step 1: 在常量块加 _K_HELICON_PRESETS_JSON**

  在 `settings_view.py` 的 `_K_CURRENT_USER = ...` 行之后添加：

  ```python
  _K_HELICON_PRESETS_JSON = "helicon/presets_json"
  ```

- [ ] **Step 2: 重构 `_build_tab_helicon` 中的 preset_box**

  将现有 `preset_box = QGroupBox("合成参数预设")` 段（约 37 行）替换为包含预设列表 + 名称输入 + 三个按钮的完整 CRUD UI：

  ```python
  # ── 合成参数预设 CRUD ──────────────────────────────────────────────────────
  preset_box = QGroupBox("合成参数预设")
  preset_v = QVBoxLayout(preset_box)
  preset_v.setSpacing(8)

  # Preset list
  self._preset_list = QListWidget()
  self._preset_list.setFixedHeight(100)
  self._preset_list.setAlternatingRowColors(True)
  self._preset_list.setToolTip("已保存的合成参数预设，双击应用")
  self._preset_list.itemDoubleClicked.connect(self._apply_selected_preset)
  preset_v.addWidget(self._preset_list)

  # Parameter form (method / radius / smoothing / quality)
  preset_form = QFormLayout()
  preset_form.setHorizontalSpacing(16)
  preset_form.setVerticalSpacing(8)

  self._method_combo = QComboBox()
  self._method_combo.addItems(["A — 加权平均 (1)", "B — 景深图 (2)", "C — 金字塔 (3)"])
  self._method_combo.setToolTip("-mp: 参数，A=1 B=2 C=3")
  self._method_combo.currentIndexChanged.connect(self._save_helicon)
  preset_form.addRow("合成方式 (-mp)", self._method_combo)

  self._radius_spin = QSpinBox()
  self._radius_spin.setRange(1, 16)
  self._radius_spin.setValue(4)
  self._radius_spin.setToolTip("-rp: 参数，范围 1–16，推荐 4")
  self._radius_spin.valueChanged.connect(self._save_helicon)
  preset_form.addRow("半径 (-rp)", self._radius_spin)

  self._smoothing_spin = QSpinBox()
  self._smoothing_spin.setRange(0, 8)
  self._smoothing_spin.setValue(4)
  self._smoothing_spin.setToolTip("-sp: 参数，范围 0–8，推荐 4")
  self._smoothing_spin.valueChanged.connect(self._save_helicon)
  preset_form.addRow("平滑度 (-sp)", self._smoothing_spin)

  self._quality_spin = QSpinBox()
  self._quality_spin.setRange(70, 100)
  self._quality_spin.setValue(95)
  self._quality_spin.setToolTip("-j: JPEG 质量，仅当输出格式为 JPEG 时有效")
  self._quality_spin.valueChanged.connect(self._save_helicon)
  preset_form.addRow("JPEG 质量 (-j)", self._quality_spin)

  preset_v.addLayout(preset_form)

  # Preset name input + action buttons
  preset_name_row = QHBoxLayout()
  preset_name_row.setContentsMargins(0, 0, 0, 0)
  preset_name_row.setSpacing(8)

  preset_name_lbl = QLabel("预设名称")
  preset_name_lbl.setFixedWidth(60)
  preset_name_lbl.setStyleSheet(f"font-size: 12px; color: {_C_MUTED};")
  self._preset_name_edit = QLineEdit()
  self._preset_name_edit.setPlaceholderText("输入预设名称后保存")
  self._preset_name_edit.setMaxLength(60)
  preset_name_row.addWidget(preset_name_lbl)
  preset_name_row.addWidget(self._preset_name_edit, stretch=1)
  preset_v.addLayout(preset_name_row)

  preset_btn_row = QHBoxLayout()
  preset_btn_row.setContentsMargins(0, 0, 0, 0)
  preset_btn_row.setSpacing(8)

  self._save_preset_btn = QPushButton("保存为预设")
  self._save_preset_btn.setStyleSheet(_btn_style("primary"))
  self._save_preset_btn.clicked.connect(self._save_current_as_preset)

  self._apply_preset_btn = QPushButton("应用选中预设")
  self._apply_preset_btn.setStyleSheet(_btn_style("outline"))
  self._apply_preset_btn.clicked.connect(self._apply_selected_preset)

  self._delete_preset_btn = QPushButton("删除选中预设")
  self._delete_preset_btn.setStyleSheet(_btn_style("outline"))
  self._delete_preset_btn.clicked.connect(self._delete_selected_preset)

  preset_btn_row.addWidget(self._save_preset_btn)
  preset_btn_row.addWidget(self._apply_preset_btn)
  preset_btn_row.addWidget(self._delete_preset_btn)
  preset_btn_row.addStretch()
  preset_v.addLayout(preset_btn_row)

  tab.body.addWidget(preset_box)
  tab.body.addStretch()
  ```

- [ ] **Step 3: 语法检查**

  Run: `python -m py_compile /mnt/n/claude/photo-platform-ydy-v3/app/views/settings_view.py`
  Expected: no output (clean)

---

### Task 2: 添加预设 CRUD 逻辑方法

**Files:**
- Modify: `app/views/settings_view.py:680–710` （Load/save helpers 段后面）

- [ ] **Step 1: 在 `_save_user` 之后添加预设 CRUD 方法**

  ```python
  # ── Helicon preset CRUD ───────────────────────────────────────────
  
  def _load_presets(self) -> list[dict]:
      """Read preset list from QSettings (JSON)."""
      import json
      qs = self.ctx.settings._qs
      raw = qs.value(_K_HELICON_PRESETS_JSON, "[]")
      try:
          data = json.loads(str(raw))
          if isinstance(data, list):
              return data
      except (json.JSONDecodeError, TypeError):
          pass
      return []
  
  def _save_preset_list(self, presets: list[dict]) -> None:
      """Persist preset list to QSettings."""
      import json
      qs = self.ctx.settings._qs
      qs.setValue(_K_HELICON_PRESETS_JSON, json.dumps(presets))
      self.ctx.settings.sync()
  
  def _refresh_preset_list_widget(self) -> None:
      """Reload QListWidget from QSettings."""
      self._preset_list.clear()
      for p in self._load_presets():
          self._preset_list.addItem(p.get("name", ""))
  
  def _save_current_as_preset(self) -> None:
      """保存为预设 — upsert by name (mirrors server.js:2449-2452)."""
      name = self._preset_name_edit.text().strip()
      if not name:
          return  # 空名称：静默忽略
      from datetime import datetime, timezone
      params = {
          "method": self._method_combo.currentIndex() + 1,
          "radius": self._radius_spin.value(),
          "smoothing": self._smoothing_spin.value(),
          "quality": self._quality_spin.value(),
      }
      preset = {
          "name": name,
          "params": params,
          "updatedAt": datetime.now(timezone.utc).isoformat(),
      }
      presets = self._load_presets()
      existing = next((i for i, p in enumerate(presets) if p.get("name") == name), -1)
      if existing >= 0:
          presets[existing] = preset
      else:
          presets.append(preset)
      self._save_preset_list(presets)
      self._refresh_preset_list_widget()
  
  def _apply_selected_preset(self) -> None:
      """应用选中预设 — fill spinboxes + save."""
      item = self._preset_list.currentItem()
      if not item:
          return
      name = item.text()
      presets = self._load_presets()
      preset = next((p for p in presets if p.get("name") == name), None)
      if not preset:
          return
      params = preset.get("params", {})
      method_idx = int(params.get("method", 1)) - 1  # 1-based → 0-based index
      method_idx = max(0, min(method_idx, self._method_combo.count() - 1))
      self._method_combo.setCurrentIndex(method_idx)
      self._radius_spin.setValue(int(params.get("radius", 4)))
      self._smoothing_spin.setValue(int(params.get("smoothing", 4)))
      self._quality_spin.setValue(int(params.get("quality", 95)))
      self._save_helicon()
  
  def _delete_selected_preset(self) -> None:
      """删除选中预设 — remove from list (mirrors server.js:2462-2471)."""
      item = self._preset_list.currentItem()
      if not item:
          return
      name = item.text()
      presets = [p for p in self._load_presets() if p.get("name") != name]
      self._save_preset_list(presets)
      self._preset_name_edit.clear()
      self._refresh_preset_list_widget()
  ```

- [ ] **Step 2: 在 `_load_all` 中加载预设列表**

  在 `_load_all` 的最后（`# User tab` 块之后）添加：

  ```python
      # Preset list widget
      self._refresh_preset_list_widget()
  ```

- [ ] **Step 3: 语法检查**

  Run: `python -m py_compile /mnt/n/claude/photo-platform-ydy-v3/app/views/settings_view.py`
  Expected: no output

---

### Task 3: 更新 `_load_all` 调用顺序并加 `_K_HELICON_PRESETS_JSON` 到模块导出

**Files:**
- Modify: `app/views/settings_view.py`

- [ ] **Step 1: 确认 `_load_all` 末尾已有 `_refresh_preset_list_widget()` 调用**

  在 `_load_all` 方法的 `# User tab` 块之后找到以下行并确认存在（Task 2 已添加）：
  ```python
      self._refresh_preset_list_widget()
  ```

- [ ] **Step 2: 运行所有现有测试，确认不破坏原有通过项**

  Run: `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_settings_view.py -v`
  Expected: `34 passed` (所有原有测试继续通过)

---

### Task 4: 为预设 CRUD 添加测试

**Files:**
- Modify: `tests/test_settings_view.py`

- [ ] **Step 1: 在 import 块添加 `_K_HELICON_PRESETS_JSON`**

  找到：
  ```python
  from app.views.settings_view import (
      SettingsView,
      APP_VERSION,
      _K_DELETE_JPG,
      _K_CURRENT_USER,
      _K_JXL_EFFORT,
      _K_HELICON_EXE,
      _K_HELICON_METHOD,
      _K_HELICON_RADIUS,
      _K_HELICON_SMOOTHING,
      _K_HELICON_QUALITY,
      _K_INCOMING_SUBDIR,
      _K_RESULTS_SUBDIR,
      _K_RECENT_PROJECTS,
  )
  ```

  替换为（末尾加 `_K_HELICON_PRESETS_JSON,`）：
  ```python
  from app.views.settings_view import (
      SettingsView,
      APP_VERSION,
      _K_DELETE_JPG,
      _K_CURRENT_USER,
      _K_JXL_EFFORT,
      _K_HELICON_EXE,
      _K_HELICON_METHOD,
      _K_HELICON_RADIUS,
      _K_HELICON_SMOOTHING,
      _K_HELICON_QUALITY,
      _K_INCOMING_SUBDIR,
      _K_RESULTS_SUBDIR,
      _K_RECENT_PROJECTS,
      _K_HELICON_PRESETS_JSON,
  )
  ```

- [ ] **Step 2: 在 `TestSettingsKeys` 末尾添加预设 key 断言**

  ```python
      def test_helicon_presets_json_key(self) -> None:
          assert _K_HELICON_PRESETS_JSON == "helicon/presets_json"
  ```

- [ ] **Step 3: 在文件末尾添加 `TestPresetCRUD` 测试类**

  ```python
  # ── Preset CRUD ──────────────────────────────────────────────────────────────

  class TestPresetCRUD:
      """Test named preset save / apply / delete (mirrors server.js /api/helicon/presets)."""

      def test_save_preset_stores_in_settings(self, view: SettingsView) -> None:
          view._preset_name_edit.setText("标准景深")
          view._method_combo.setCurrentIndex(1)   # B
          view._radius_spin.setValue(6)
          view._smoothing_spin.setValue(2)
          view._quality_spin.setValue(90)
          view._save_current_as_preset()
          presets = view._load_presets()
          assert len(presets) == 1
          assert presets[0]["name"] == "标准景深"
          assert presets[0]["params"]["method"] == 2   # index+1
          assert presets[0]["params"]["radius"] == 6
          assert presets[0]["params"]["smoothing"] == 2
          assert presets[0]["params"]["quality"] == 90

      def test_save_preset_upserts_existing_name(self, view: SettingsView) -> None:
          """Saving with the same name should overwrite, not duplicate."""
          view._preset_name_edit.setText("my-preset")
          view._radius_spin.setValue(4)
          view._save_current_as_preset()
          view._radius_spin.setValue(8)
          view._save_current_as_preset()
          presets = view._load_presets()
          assert len(presets) == 1
          assert presets[0]["params"]["radius"] == 8

      def test_empty_preset_name_not_saved(self, view: SettingsView) -> None:
          """Empty name → silent no-op, list stays empty."""
          view._preset_name_edit.setText("")
          view._save_current_as_preset()
          assert view._load_presets() == []

      def test_apply_preset_fills_spinboxes(self, view: SettingsView) -> None:
          # First save a preset
          view._preset_name_edit.setText("应用测试预设")
          view._method_combo.setCurrentIndex(2)   # C
          view._radius_spin.setValue(3)
          view._smoothing_spin.setValue(6)
          view._quality_spin.setValue(80)
          view._save_current_as_preset()

          # Reset spinboxes to defaults
          view._method_combo.setCurrentIndex(0)
          view._radius_spin.setValue(4)
          view._smoothing_spin.setValue(4)
          view._quality_spin.setValue(95)

          # Select the preset in the list and apply
          view._preset_list.setCurrentRow(0)
          view._apply_selected_preset()

          assert view._method_combo.currentIndex() == 2
          assert view._radius_spin.value() == 3
          assert view._smoothing_spin.value() == 6
          assert view._quality_spin.value() == 80

      def test_apply_preset_double_click(self, view: SettingsView) -> None:
          """Double-clicking a list item applies the preset."""
          view._preset_name_edit.setText("双击测试")
          view._radius_spin.setValue(7)
          view._save_current_as_preset()

          view._radius_spin.setValue(4)  # reset
          view._preset_list.setCurrentRow(0)
          # itemDoubleClicked is connected to _apply_selected_preset; simulate via direct call
          view._apply_selected_preset()
          assert view._radius_spin.value() == 7

      def test_delete_preset_removes_from_list(self, view: SettingsView) -> None:
          view._preset_name_edit.setText("删除测试")
          view._save_current_as_preset()
          assert view._preset_list.count() == 1

          view._preset_list.setCurrentRow(0)
          view._delete_selected_preset()

          assert view._preset_list.count() == 0
          assert view._load_presets() == []

      def test_preset_list_survives_reload(self, view: SettingsView) -> None:
          """Presets persisted to QSettings survive on_activate() reload."""
          view._preset_name_edit.setText("持久化测试")
          view._radius_spin.setValue(5)
          view._save_current_as_preset()

          view.on_activate()  # reload from QSettings
          assert view._preset_list.count() == 1
          assert view._preset_list.item(0).text() == "持久化测试"

      def test_multiple_presets_in_list(self, view: SettingsView) -> None:
          """Can store and list multiple presets."""
          for name in ["预设A", "预设B", "预设C"]:
              view._preset_name_edit.setText(name)
              view._save_current_as_preset()
          assert view._preset_list.count() == 3

      def test_delete_one_of_many_presets(self, view: SettingsView) -> None:
          for name in ["第一", "第二", "第三"]:
              view._preset_name_edit.setText(name)
              view._save_current_as_preset()
          # Delete the middle one
          view._preset_list.setCurrentRow(1)
          view._delete_selected_preset()
          remaining = [p["name"] for p in view._load_presets()]
          assert "第二" not in remaining
          assert "第一" in remaining
          assert "第三" in remaining
  ```

- [ ] **Step 4: 运行所有测试（34 原有 + 新增）**

  Run: `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_settings_view.py -v`
  Expected: `43 passed` （34 原有 + 9 新增）

---

### Task 5: 截图验证

**Files:**
- Modify/create: `docs/shots/settings_func.png`

- [ ] **Step 1: 运行截图脚本**

  Run:
  ```bash
  cd /mnt/n/claude/photo-platform-ydy-v3
  QT_QPA_PLATFORM=offscreen python docs/shots/capture_settings.py
  ```
  Expected: 截图文件生成或更新 `docs/shots/page_settings.png`

  若脚本不支持 Helicon tab 预设展示，改用 Playwright offscreen 截图，或临时创建小脚本：

  ```python
  # docs/shots/capture_settings_func.py
  import os; os.environ["QT_QPA_PLATFORM"] = "offscreen"
  import sys
  from PyQt6.QtWidgets import QApplication
  from PyQt6.QtCore import QTimer
  from app.app_context import AppContext
  from app.views.settings_view import SettingsView

  app = QApplication(sys.argv)
  ctx = AppContext()
  view = SettingsView(ctx)
  view.resize(1920, 1080)
  view.on_activate()
  view.show()

  # Switch to Helicon tab (index 1) and populate a preset
  view._tabs.setCurrentIndex(1)
  view._preset_name_edit.setText("标准景深叠加")
  view._method_combo.setCurrentIndex(1)
  view._radius_spin.setValue(4)
  view._smoothing_spin.setValue(4)
  view._quality_spin.setValue(95)
  view._save_current_as_preset()

  def do_shot():
      screen = app.primaryScreen()
      pix = screen.grabWindow(0)
      if pix.isNull():
          pix = view.grab()
      pix.save("docs/shots/settings_func.png")
      print("saved docs/shots/settings_func.png")
      app.quit()

  QTimer.singleShot(200, do_shot)
  sys.exit(app.exec())
  ```

  Run:
  ```bash
  cd /mnt/n/claude/photo-platform-ydy-v3
  QT_QPA_PLATFORM=offscreen python docs/shots/capture_settings_func.py
  ```
  Expected: `saved docs/shots/settings_func.png`

---

### Task 6: Commit

- [ ] **Step 1: 确认最终全绿**

  Run: `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_settings_view.py -v`
  Expected: all passed (≥ 43)

- [ ] **Step 2: 查看 diff**

  Run: `cd /mnt/n/claude/photo-platform-ydy-v3 && git diff --stat`

- [ ] **Step 3: Commit**

  ```bash
  cd /mnt/n/claude/photo-platform-ydy-v3
  git add app/views/settings_view.py tests/test_settings_view.py docs/specs/settings-functional.md docs/shots/settings_func.png docs/superpowers/plans/2026-06-03-settings-preset-crud.md
  git commit -m "$(cat <<'EOF'
  feat(settings): 合成参数预设 CRUD + settings spec

  - 在 Helicon tab 的「合成参数预设」区域加入命名预设列表
    (QListWidget) + 保存/应用/删除按钮，持久化至
    QSettings helicon/presets_json
  - 新增 _K_HELICON_PRESETS_JSON 常量 + 对应 9 条测试
  - 写入 docs/specs/settings-functional.md（功能 Oracle）
  - 截图：docs/shots/settings_func.png

  Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
  EOF
  )"
  ```

---

## 自检 Spec 覆盖

| 需求 | 覆盖任务 |
|------|---------|
| Helicon exe 路径（自动探测三级+手动+保存+清除） | Task 1–3（已有功能，本次不改）|
| **合成参数预设 CRUD（增删改选）** | Task 1–4 |
| JXL effort | Task 1–3（已有功能） |
| **删除 JPG 默认关 + 四前置说明** | Task 1–3（已有功能） |
| 当前操作人 | 已有功能 |
| 子目录名（incoming-jpg/results） | 已有功能 |
| 最近项目 | 已有功能 |
| 关于（版本/日志目录） | 已有功能 |
| pytest 全绿 | Task 4 |
| 截图 `docs/shots/settings_func.png` | Task 5 |
| Commit | Task 6 |
