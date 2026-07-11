"""金标 + 只读接口测试 —— 工作台不再掏卡片的私有成员。

场景: app/views/workbench_*.py 里有 39 处直接伸手掏别的控件的私有变量
  (self._naming._province、self._grouping._grouping、self._monitor._on_select_none()…)。
  卡片内部改个字段名, 工作台就静默崩; 卡片也没法脱离工作台单测。

红线: **主处理逻辑一行不能变** —— UID 拼段 / 整理 payload / 分组输出名。
  所以这里先钉一根金标: 填满命名卡, 抓出送进 UID 推导与整理流程的那个 dict,
  期望值**硬编码**(不引用实现), 重构前后必须逐字节一致。

(Fable 5, 2026-07-12, 用户: "你确保功能不变吗, 我说的是我主要处理逻辑")
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.widgets.naming_panel import NamingPanel
from app.views.workbench_specimen_identity import WorkbenchSpecimenIdentityMixin


class _IdentityHarness(WorkbenchSpecimenIdentityMixin):
    """只带一个命名卡的最小宿主 —— 只为触发 _current_naming_result_values。"""

    def __init__(self, naming) -> None:
        self._naming = naming


@pytest.fixture
def naming(qtbot):
    ctx = MagicMock()
    ctx.get_db.return_value = None
    panel = NamingPanel(ctx)
    qtbot.addWidget(panel)
    # 故意带前后空格 —— 现有逻辑一律 .strip(), 接口化后必须照旧 strip。
    panel._province.setText(" GXFCG ")
    panel._site.setText("BLW")
    panel._station.setText("S3")
    panel._species_id.setText("SC001")
    panel._storage.setText(" D79 ")
    panel._collection_date.setText("20260618")
    panel._photo_date.setText("20260620")
    panel._photo_notes.setPlainText(" 潮下带 ")
    panel._seq.setValue(3)
    return panel


# ── 金标: 主处理逻辑的入口 dict ─────────────────────────────────────────────
GOLDEN_RESULT_VALUES = {
    "province": "GXFCG",
    "site": "BLW",
    "station": "S3",
    "species_id": "SC001",
    "storage": "D79",
    "date_seg": "20260618-0620",   # 采集/拍摄日期不同 -> 双段
    "collection_date": "20260618",
    "photo_date": "20260620",
}


def test_golden_result_values_unchanged(naming):
    """重构前后, 送进 UID 推导/成果命名的 dict 必须逐字段一致。"""
    assert _IdentityHarness(naming)._current_naming_result_values() == GOLDEN_RESULT_VALUES


# ── 新接口: 与旧的私有读法逐一等值 ──────────────────────────────────────────
def test_accessors_equal_old_private_reads(naming):
    assert naming.province() == naming._province.text().strip() == "GXFCG"
    assert naming.site() == naming._site.text().strip() == "BLW"
    assert naming.station() == naming._station.text().strip() == "S3"
    assert naming.species_id() == naming._species_id.text().strip() == "SC001"
    assert naming.storage_code() == naming._storage.text().strip() == "D79"
    assert naming.collection_date() == naming._collection_date.text().strip() == "20260618"
    assert naming.photo_date() == naming._photo_date.text().strip() == "20260620"
    assert naming.photo_notes() == naming._photo_notes.toPlainText().strip() == "潮下带"
    assert naming.sequence() == naming._seq.value() == 3


def test_set_photo_date_writes_widget(naming):
    naming.set_photo_date("20260701")
    assert naming._photo_date.text() == "20260701"
    assert naming.photo_date() == "20260701"


def test_fields_edited_signal_fires_on_date_and_notes(naming, qtbot):
    """工作台原本直接连内部 QLineEdit.textEdited 做右栏自动存草稿。
    换成面板级 fields_edited 信号 —— 触发点必须一模一样。
    """
    with qtbot.waitSignal(naming.fields_edited, timeout=1000):
        naming._photo_notes.setPlainText("改了备注")   # textChanged
    with qtbot.waitSignal(naming.fields_edited, timeout=1000):
        naming._collection_date.textEdited.emit("20260619")
    with qtbot.waitSignal(naming.fields_edited, timeout=1000):
        naming._photo_date.textEdited.emit("20260621")
