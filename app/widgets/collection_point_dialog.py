"""collection_point_dialog.py — 采集地图「新增/绑定采集点」对话框.

采集地图页两种落点入口（点空白 / 手输经纬度 / 拖点移动）共用本对话框的
「新建 / 绑定」二选一身份录入：

  - 新建站位：填 站位 + 采集日期（地区/样地自动套用当前项目，可改）→
    由调用方 ``crs.upsert_record`` 写一条采集记录。
  - 绑定现有站位：从本项目已有站位下拉选一个 → 由调用方
    ``crs.set_station_coords`` 把该站位全部行的经纬度刷成新值
    （采集地图按站位聚合质心，只改一行点不动）。

经纬度在对话框顶部可微调（点选/手输带入，仍允许人工校正）。
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtGui import QDoubleValidator

from app.widgets._form_row import form_row

_ZONE_LABELS = {"intertidal": "潮间带", "subtidal": "潮下带"}


def _fmt_coord(v: float) -> str:
    """经纬度回填文本：保留 6 位小数。"""
    try:
        return f"{float(v):.6f}"
    except (TypeError, ValueError):
        return ""


class CollectionPointDialog(QDialog):
    """新建 / 绑定一个采集点。``exec()`` 返回 Accepted 后用 ``result_point()`` 取动作。"""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        lon: Optional[float] = None,
        lat: Optional[float] = None,
        province: str = "",
        site: str = "",
        zone: str = "intertidal",
        stations: Optional[list[dict]] = None,
        mode: str = "new",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("采集点")
        self.setModal(True)
        self.setMinimumWidth(360)
        self._stations: list[dict] = list(stations or [])
        self._initial_zone = zone if zone in _ZONE_LABELS else "intertidal"
        self._initial_mode = "bind" if mode == "bind" and self._stations else "new"
        self._build_ui(lon, lat, province, site)

    # ── UI ──────────────────────────────────────────────────────────────────

    def _build_ui(self, lon: Optional[float], lat: Optional[float], province: str, site: str) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        coord = QFormLayout()
        coord.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._lon_edit = QLineEdit(_fmt_coord(lon))
        self._lat_edit = QLineEdit(_fmt_coord(lat))
        self._lon_edit.setPlaceholderText("如 121.6543")
        self._lat_edit.setPlaceholderText("如 28.1234")
        self._lon_edit.setToolTip("十进制度 WGS-84，范围 [-180, 180]。点选带入，可微调或手输。")
        self._lat_edit.setToolTip("十进制度 WGS-84，范围 [-90, 90]。点选带入，可微调或手输。")
        coord.addRow("经度", self._lon_edit)
        coord.addRow("纬度", self._lat_edit)
        self._zone_combo = QComboBox()
        for z, lbl in _ZONE_LABELS.items():
            self._zone_combo.addItem(lbl, z)
        self._zone_combo.setCurrentIndex(0 if self._initial_zone == "intertidal" else 1)
        coord.addRow("采区", self._zone_combo)
        lay.addLayout(coord)

        # 模式单选：新建 / 绑定
        mode_row = QHBoxLayout()
        mode_row.setSpacing(16)
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._rb_new = QRadioButton("新建站位")
        self._rb_bind = QRadioButton("绑定现有站位")
        self._rb_bind.setEnabled(bool(self._stations))
        self._mode_group.addButton(self._rb_new)
        self._mode_group.addButton(self._rb_bind)
        mode_row.addWidget(self._rb_new)
        mode_row.addWidget(self._rb_bind)
        mode_row.addStretch()
        lay.addLayout(mode_row)

        # 新建字段
        self._new_box = QWidget()
        nf = QFormLayout(self._new_box)
        nf.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._prov_edit = QLineEdit(province)
        self._site_edit = QLineEdit(site)
        self._station_edit = QLineEdit()
        self._station_edit.setPlaceholderText("如 B2")
        self._date_edit = QLineEdit(QDate.currentDate().toString("yyyyMMdd"))
        self._date_edit.setPlaceholderText("YYYYMMDD，默认今天")
        nf.addRow("地区", self._prov_edit)
        nf.addRow("样地", self._site_edit)
        nf.addRow("站位 *", self._station_edit)
        nf.addRow("采集日期", self._date_edit)
        lay.addWidget(self._new_box)

        # 绑定下拉
        self._bind_box = QWidget()
        bf = QFormLayout(self._bind_box)
        bf.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._station_combo = QComboBox()
        for s in self._stations:
            label = self._station_label(s)
            self._station_combo.addItem(label, s)
        bf.addRow("站位", self._station_combo)
        lay.addWidget(self._bind_box)

        (self._rb_new if self._initial_mode == "new" else self._rb_bind).setChecked(True)
        self._rb_new.toggled.connect(self._on_mode_toggled)
        self._on_mode_toggled(self._rb_new.isChecked())

        # 底部按钮
        foot = QHBoxLayout()
        foot.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("保存")
        ok.setObjectName("Primary")
        ok.setDefault(True)
        ok.clicked.connect(self._accept_valid_collection_point)
        foot.addWidget(cancel)
        foot.addWidget(ok)
        lay.addLayout(foot)

    @staticmethod
    def _station_label(s: dict) -> str:
        prov = s.get("province") or ""
        site = s.get("site") or ""
        st = s.get("station") or ""
        lab = s.get("station_label") or ""
        cnt = s.get("count")
        tail = f"（{cnt} 条）" if cnt else ""
        mid = f" {lab}" if lab else ""
        return f"{prov}-{site}-{st}{mid}{tail}".strip()

    def _on_mode_toggled(self, new_checked: bool) -> None:
        self._new_box.setVisible(new_checked)
        self._bind_box.setVisible(not new_checked)

    # ── accept + 结果 ───────────────────────────────────────────────────────

    def _parse_coord(self, edit: QLineEdit, lo: float, hi: float, name: str) -> Optional[float]:
        raw = edit.text().strip()
        try:
            v = float(raw)
        except ValueError:
            QMessageBox.warning(self, "采集点", f"{name}必须是数字（十进制度）。")
            return None
        if not (lo <= v <= hi):
            QMessageBox.warning(self, "采集点", f"{name}超出范围：{lo}~{hi}。")
            return None
        return v

    def _accept_valid_collection_point(self) -> None:
        lon = self._parse_coord(self._lon_edit, -180.0, 180.0, "经度")
        if lon is None:
            return
        lat = self._parse_coord(self._lat_edit, -90.0, 90.0, "纬度")
        if lat is None:
            return

        if self._rb_new.isChecked():
            prov = self._prov_edit.text().strip()
            site = self._site_edit.text().strip()
            station = self._station_edit.text().strip()
            date = self._date_edit.text().strip()
            missing = []
            if not prov:
                missing.append("地区")
            if not site:
                missing.append("样地")
            if not station:
                missing.append("站位")
            if not date:
                missing.append("采集日期")
            if missing:
                QMessageBox.warning(self, "采集点", "请填写：" + "、".join(missing))
                return
            self._result = {
                "action": "new",
                "province": prov, "site": site, "station": station,
                "collection_date": date, "zone": self._zone_combo.currentData(),
                "lon": lon, "lat": lat,
            }
        else:
            s = self._station_combo.currentData()
            if not isinstance(s, dict):
                QMessageBox.warning(self, "采集点", "请选择要绑定的站位。")
                return
            self._result = {
                "action": "bind",
                "province": s.get("province"), "site": s.get("site"),
                "station": s.get("station"),
                "lon": lon, "lat": lat,
            }
        self.accept()

    def result_point(self) -> Optional[dict]:
        """``exec()`` 返回 Accepted 后取动作字典；未确认返回 None。"""
        return getattr(self, "_result", None)

    # ── 测试钩子 ─────────────────────────────────────────────────────────────

    def set_mode(self, mode: str) -> None:
        if mode == "bind" and self._rb_bind.isEnabled():
            self._rb_bind.setChecked(True)
        else:
            self._rb_new.setChecked(True)
