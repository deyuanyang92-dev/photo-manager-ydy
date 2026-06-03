"""coords_view.py — 坐标工具视图。

功能：
  - 多格式坐标输入（DD / DMS / DDM / ISO6709）自动解析
  - 解析结果徽章（格式类型 + lat/lon）
  - WGS-84 / GCJ-02 / BD09 坐标系卡片（十进制 / 度分秒 / 度分）
  - 交互地图（QWebEngineView 内嵌高德地图）
      · 拖 marker 选点
      · 搜索地名（高德 PlaceSearch）
      · 反向地理编码（地图点击 → 地名）
  - 地图原地更新，不重建整页（对应 web coordUpdateBadgeAndCards 约束）

Oracle: prototype-photo-gui/app.js ~line 13270-13540
        prototype-photo-gui/coord-utils.js

已知限制（与 web 版保持一致）：
  P3-5: gcj02_to_wgs84 5 次迭代误差 < 1 m
  P3-6: verbatim_lon 始终空串
"""
from __future__ import annotations

import json
from typing import Optional, TYPE_CHECKING

from PyQt6.QtCore import Qt, QUrl, pyqtSlot
from PyQt6.QtGui import QClipboard
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.views.base_view import BaseView
from app.utils.coord_utils import (
    parse_detailed,
    to_dd_zh,
    to_dms_zh,
    to_ddm_zh,
    wgs84_to_gcj02,
    wgs84_to_bd09,
    gcj02_to_wgs84,
    is_valid,
)

if TYPE_CHECKING:
    from app.app_context import AppContext

# ── 高德地图凭证（来源：app.js line 31） ──────────────────────────────────────
_AMAP_KEY = "f9b9d89f08a91d7320a879970a784043"
_AMAP_SECURITY_CODE = "8d7ff2ed7f5e5dfd9fe0ba97e3616aa6"

# ── 地图 HTML 模板 ─────────────────────────────────────────────────────────────
_MAP_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #1a1a1a; color: #ccc; font-family: system-ui, sans-serif; font-size: 13px; }
  #map { width: 100%; height: 100vh; }
  #info { position: absolute; bottom: 0; left: 0; right: 0;
          background: rgba(0,0,0,0.75); padding: 6px 10px; z-index: 200; }
  #coordDisplay { color: #e0e0e0; margin-bottom: 2px; }
  #coordFormats { color: #aaa; font-size: 12px; }
</style>
</head>
<body>
<div id="map"></div>
<div id="info">
  <div id="coordDisplay">点击地图或拖拽标记选点</div>
  <div id="coordFormats"></div>
</div>

<script>
window._securityConfig = { securityJsCode: "{SECURITY_CODE}" };
(function() {
  window._AMapSecurityConfig = window._securityConfig;
})();
</script>
<script src="https://webapi.amap.com/maps?v=2.0&key={AMAP_KEY}&plugin=AMap.Geocoder,AMap.PlaceSearch"
        onerror="document.getElementById('coordDisplay').textContent='高德地图加载失败（需网络连接）'">
</script>
<script>
var _map = null;
var _marker = null;
var _selectedWgs = null;

function initMap(lat, lon) {
  if (!window.AMap) {
    document.getElementById('coordDisplay').textContent = '高德地图加载失败';
    return;
  }
  var center = (lat !== null && lon !== null)
    ? [lon, lat]
    : [121.76, 29.11];

  _map = new AMap.Map('map', {
    zoom: 12,
    center: center,
    resizeEnable: true,
  });

  AMap.plugin(['AMap.ToolBar'], function() {
    _map.addControl(new AMap.ToolBar({ position: 'RT' }));
  });

  _map.on('click', function(e) {
    setMarker(e.lnglat.getLng(), e.lnglat.getLat());
  });

  if (lat !== null && lon !== null) {
    setMarker(lon, lat);  // lon/lat are already GCJ-02 at call site
  }

  setTimeout(function() { if (_map) _map.resize(); }, 300);
}

function setMarker(gcjLon, gcjLat) {
  if (_marker) _map.remove(_marker);
  _marker = new AMap.Marker({ position: [gcjLon, gcjLat], draggable: true, cursor: 'move' });
  _marker.on('dragend', function(e) {
    var pos = e.target.getPosition();
    updateDisplay(pos.getLng(), pos.getLat());
  });
  _map.add(_marker);
  updateDisplay(gcjLon, gcjLat);
}

function updateDisplay(gcjLon, gcjLat) {
  // Convert GCJ-02 → WGS-84 inline (5-iteration, ~1 m error)
  var wLon = gcjLon, wLat = gcjLat;
  for (var i = 0; i < 5; i++) {
    var g = wgs84ToGcj02(wLon, wLat);
    wLon += gcjLon - g.lon;
    wLat += gcjLat - g.lat;
  }
  _selectedWgs = { lat: +wLat.toFixed(7), lon: +wLon.toFixed(7) };
  document.getElementById('coordDisplay').textContent =
    'WGS-84: ' + _selectedWgs.lat.toFixed(6) + ', ' + _selectedWgs.lon.toFixed(6) +
    '  (GCJ-02: ' + gcjLat.toFixed(6) + ', ' + gcjLon.toFixed(6) + ')';
  document.getElementById('coordFormats').textContent =
    'DMS: ' + toDMS(_selectedWgs.lat, _selectedWgs.lon) +
    '  |  DDM: ' + toDDM(_selectedWgs.lat, _selectedWgs.lon);
  // Notify Python host
  if (window.qt_bridge) {
    window.qt_bridge.onMarkerMoved(JSON.stringify(_selectedWgs));
  }
}

function doPlaceSearch(q) {
  if (!q || !window.AMap) return;
  AMap.plugin('AMap.PlaceSearch', function() {
    var ps = new AMap.PlaceSearch({ pageSize: 1, pageIndex: 1 });
    ps.search(q, function(status, result) {
      if (status === 'complete' && result.poiList && result.poiList.pois.length) {
        var poi = result.poiList.pois[0];
        var loc = poi.location;
        _map.setCenter([loc.getLng(), loc.getLat()]);
        _map.setZoom(15);
        setMarker(loc.getLng(), loc.getLat());
      }
    });
  });
}

function moveTo(gcjLon, gcjLat) {
  if (!_map) return;
  _map.setCenter([gcjLon, gcjLat]);
  _map.setZoom(14);
  setMarker(gcjLon, gcjLat);
}

// ── Minimal inline GCJ-02 transform (mirrors coord-utils.js) ──────────────
var PI = Math.PI, A = 6378245.0, EE = 0.00669342162296594323;
function inChina(lon, lat) {
  return lon >= 73.66 && lon <= 135.05 && lat >= 3.86 && lat <= 53.55;
}
function transLat(x, y) {
  var r = -100 + 2*x + 3*y + 0.2*y*y + 0.1*x*y + 0.2*Math.sqrt(Math.abs(x));
  r += (20*Math.sin(6*x*PI) + 20*Math.sin(2*x*PI)) * 2/3;
  r += (20*Math.sin(y*PI) + 40*Math.sin(y/3*PI)) * 2/3;
  r += (160*Math.sin(y/12*PI) + 320*Math.sin(y*PI/30)) * 2/3;
  return r;
}
function transLon(x, y) {
  var r = 300 + x + 2*y + 0.1*x*y + 0.1*Math.sqrt(Math.abs(x));
  r += (20*Math.sin(6*x*PI) + 20*Math.sin(2*x*PI)) * 2/3;
  r += (20*Math.sin(x*PI) + 40*Math.sin(x/3*PI)) * 2/3;
  r += (150*Math.sin(x/12*PI) + 300*Math.sin(x/30*PI)) * 2/3;
  return r;
}
function wgs84ToGcj02(lon, lat) {
  if (!inChina(lon, lat)) return { lon: lon, lat: lat };
  var dLat = transLat(lon-105, lat-35);
  var dLon = transLon(lon-105, lat-35);
  var rad = lat/180*PI, magic = Math.sin(rad);
  magic = 1 - EE*magic*magic;
  var sq = Math.sqrt(magic);
  dLat = dLat*180/((A*(1-EE))/(magic*sq)*PI);
  dLon = dLon*180/((A/sq)/Math.cos(rad)*PI);
  return { lon: +(lon+dLon).toFixed(6), lat: +(lat+dLat).toFixed(6) };
}
function toDMS(lat, lon) {
  function fmt(dd) {
    var a = Math.abs(dd), d = Math.floor(a), mf = (a-d)*60, m = Math.floor(mf), s = ((mf-m)*60).toFixed(1);
    return d + '°' + m + "'" + s + '"';
  }
  return fmt(lat)+(lat>=0?'N':'S')+' '+fmt(lon)+(lon>=0?'E':'W');
}
function toDDM(lat, lon) {
  function fmt(dd) {
    var a = Math.abs(dd), d = Math.floor(a), m = ((a-d)*60).toFixed(3);
    return d + '°' + m + "'";
  }
  return fmt(lat)+(lat>=0?'N':'S')+' '+fmt(lon)+(lon>=0?'E':'W');
}
</script>
</body>
</html>
""".replace("{AMAP_KEY}", _AMAP_KEY).replace("{SECURITY_CODE}", _AMAP_SECURITY_CODE)


class CoordsView(BaseView):
    """坐标工具主视图。

    实现坐标多格式解析、坐标系转换卡片，以及内嵌高德交互地图。
    地图通过 QWebEngineView 内嵌，原地更新 marker 而不重建整页（
    对应 web 版 coordUpdateBadgeAndCards 不调 render() 的约束）。
    """

    view_id = "coords"
    nav_title = "坐标工具"
    nav_icon = "📍"

    # 格式切换选项
    _FMT_OPTIONS = [("十进制", "dd"), ("度分秒", "dms"), ("度分", "ddm")]

    def __init__(self, ctx: "AppContext") -> None:
        self._parsed: Optional[dict] = None
        self._cs_fmt: str = "dd"  # current coordinate-system tab
        self._map_open: bool = False
        super().__init__(ctx)

    # ── BaseView interface ────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # ── 标题 ──────────────────────────────────────────────────────────────
        title = QLabel("📍 坐标工具")
        title.setObjectName("PageTitle")
        title.setStyleSheet("font-size: 18px; font-weight: bold; background: transparent;")
        root.addWidget(title)

        # ── 输入区 ─────────────────────────────────────────────────────────────
        input_box = QGroupBox("坐标输入（支持 DD / DMS / DDM / ISO 6709 格式）")
        input_layout = QVBoxLayout(input_box)
        input_layout.setSpacing(6)

        self._input_edit = QLineEdit()
        self._input_edit.setPlaceholderText(
            "例：29.11492 N 121.76421 E  /  29°06'53.7\"N 121°45'51.2\"E  /  +29.11492+121.76421/"
        )
        self._input_edit.setMinimumHeight(32)
        self._input_edit.textChanged.connect(self._on_input_changed)
        input_layout.addWidget(self._input_edit)

        # 解析结果徽章
        self._badge = QLabel("")
        self._badge.setObjectName("CoordBadge")
        self._badge.setWordWrap(True)
        self._badge.setVisible(False)
        self._badge.setStyleSheet(
            "padding: 4px 8px; border-radius: 4px; font-size: 12px; background: transparent;"
        )
        input_layout.addWidget(self._badge)

        root.addWidget(input_box)

        # ── 格式选项 + 坐标系卡片区 ────────────────────────────────────────────
        cs_box = QGroupBox("坐标系转换")
        cs_layout = QVBoxLayout(cs_box)
        cs_layout.setSpacing(8)

        # 格式 tab（十进制 / 度分秒 / 度分）
        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(6)
        self._fmt_group = QButtonGroup(self)
        for label, key in self._FMT_OPTIONS:
            rb = QRadioButton(label)
            rb.setProperty("fmt_key", key)
            rb.setChecked(key == self._cs_fmt)
            rb.toggled.connect(self._on_fmt_changed)
            self._fmt_group.addButton(rb)
            fmt_row.addWidget(rb)
        fmt_row.addStretch()
        cs_layout.addLayout(fmt_row)

        # 坐标系卡片容器
        self._cs_cards_widget = QWidget()
        self._cs_cards_layout = QHBoxLayout(self._cs_cards_widget)
        self._cs_cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cs_cards_layout.setSpacing(10)
        cs_layout.addWidget(self._cs_cards_widget)
        self._cs_cards_widget.setVisible(False)

        root.addWidget(cs_box)

        # ── 地图区 ─────────────────────────────────────────────────────────────
        map_header = QHBoxLayout()
        self._map_toggle_btn = QPushButton("打开交互地图")
        self._map_toggle_btn.setCheckable(True)
        self._map_toggle_btn.setFixedWidth(120)
        self._map_toggle_btn.clicked.connect(self._on_map_toggle)
        map_header.addWidget(self._map_toggle_btn)

        self._place_input = QLineEdit()
        self._place_input.setPlaceholderText("搜索地名...")
        self._place_input.setFixedWidth(200)
        self._place_input.returnPressed.connect(self._on_place_search)
        map_header.addWidget(self._place_input)

        search_btn = QPushButton("搜索")
        search_btn.setFixedWidth(60)
        search_btn.clicked.connect(self._on_place_search)
        map_header.addWidget(search_btn)
        map_header.addStretch()

        root.addLayout(map_header)

        # 地图容器（懒加载 QWebEngineView）
        self._map_container = QWidget()
        self._map_container.setVisible(False)
        self._map_container.setMinimumHeight(420)
        self._map_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        map_container_layout = QVBoxLayout(self._map_container)
        map_container_layout.setContentsMargins(0, 0, 0, 0)
        self._web_view = None  # lazily created when map opens
        root.addWidget(self._map_container)

        # 地图选点确认行
        self._map_confirm_row = QHBoxLayout()
        self._map_coord_label = QLabel("")
        self._map_coord_label.setStyleSheet(
            "font-size: 12px; color: #aaa; background: transparent;"
        )
        self._map_confirm_btn = QPushButton("确认选点")
        self._map_confirm_btn.setEnabled(False)
        self._map_confirm_btn.clicked.connect(self._on_map_confirm)
        self._map_confirm_row.addWidget(self._map_coord_label, 1)
        self._map_confirm_row.addWidget(self._map_confirm_btn)
        self._map_confirm_container = QWidget()
        self._map_confirm_container.setLayout(self._map_confirm_row)
        self._map_confirm_container.setVisible(False)
        root.addWidget(self._map_confirm_container)

        root.addStretch()

        # Selected WGS-84 coord from map (set via JS bridge callback)
        self._map_selected_wgs: Optional[dict] = None

    def on_activate(self) -> None:
        """Called each time user navigates to this view."""
        pass

    # ── Input handling ────────────────────────────────────────────────────────

    def _on_input_changed(self, text: str) -> None:
        """Parse input in-place and update badge + cards without rebuilding page."""
        if not text.strip():
            self._parsed = None
            self._update_badge()
            self._update_cs_cards()
            return
        self._parsed = parse_detailed(text) or None
        self._update_badge()
        self._update_cs_cards()
        # If map is open, move marker to new coordinates
        if self._map_open and self._parsed and self._web_view:
            gcj = wgs84_to_gcj02(self._parsed["lon"], self._parsed["lat"])
            self._call_map_js(
                f"moveTo({gcj['lon']}, {gcj['lat']})"
            )

    def _on_fmt_changed(self, checked: bool) -> None:
        if not checked:
            return
        btn = self.sender()
        if btn:
            self._cs_fmt = btn.property("fmt_key") or "dd"
            self._update_cs_cards()

    # ── Badge update (in-place, no rebuild) ──────────────────────────────────

    def _update_badge(self) -> None:
        """Update parse-result badge text and style in-place."""
        text = self._input_edit.text().strip()
        if not text:
            self._badge.setVisible(False)
            return
        self._badge.setVisible(True)
        if self._parsed:
            p = self._parsed
            self._badge.setText(
                f"✓ {p['format_label']} — lat {p['lat']:.6f}, lon {p['lon']:.6f}"
            )
            self._badge.setStyleSheet(
                "padding: 4px 8px; border-radius: 4px; font-size: 12px;"
                "background: rgba(56,160,90,0.18); color: #5ccb7f; border: 1px solid #3a7a4a;"
            )
        else:
            self._badge.setText("无法识别坐标格式")
            self._badge.setStyleSheet(
                "padding: 4px 8px; border-radius: 4px; font-size: 12px;"
                "background: rgba(180,40,40,0.18); color: #e07070; border: 1px solid #7a3a3a;"
            )

    # ── Coordinate-system cards (in-place update) ─────────────────────────────

    def _update_cs_cards(self) -> None:
        """Rebuild the three coordinate-system cards in-place."""
        # Clear existing cards
        while self._cs_cards_layout.count():
            item = self._cs_cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._parsed:
            self._cs_cards_widget.setVisible(False)
            return

        self._cs_cards_widget.setVisible(True)
        p = self._parsed
        lat, lon = p["lat"], p["lon"]

        gcj = wgs84_to_gcj02(lon, lat)
        bd = wgs84_to_bd09(lon, lat)

        systems = [
            ("WGS-84", "国际通用", lat, lon),
            ("GCJ-02", "国测局", gcj["lat"], gcj["lon"]),
            ("BD09", "百度", bd["lat"], bd["lon"]),
        ]

        fmt_fn = {
            "dd": to_dd_zh,
            "dms": to_dms_zh,
            "ddm": to_ddm_zh,
        }[self._cs_fmt]

        for name, sub, clat, clon in systems:
            card = self._make_cs_card(name, sub, clat, clon, fmt_fn)
            self._cs_cards_layout.addWidget(card)

        self._cs_cards_layout.addStretch()

    def _make_cs_card(
        self,
        name: str,
        sub: str,
        lat: float,
        lon: float,
        fmt_fn,
    ) -> QWidget:
        """Build a single coordinate-system card widget."""
        card = QWidget()
        card.setObjectName("CoordCard")
        card.setStyleSheet(
            "QWidget#CoordCard {"
            "  background: rgba(255,255,255,0.05);"
            "  border: 1px solid rgba(255,255,255,0.1);"
            "  border-radius: 6px;"
            "  padding: 8px 10px;"
            "}"
        )
        card.setMinimumWidth(200)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(8, 8, 8, 8)
        card_layout.setSpacing(4)

        # Header row
        header = QHBoxLayout()
        name_lbl = QLabel(name)
        name_lbl.setStyleSheet("font-weight: bold; font-size: 13px; background: transparent;")
        sub_lbl = QLabel(sub)
        sub_lbl.setStyleSheet("font-size: 11px; color: #888; background: transparent;")
        header.addWidget(name_lbl)
        header.addWidget(sub_lbl)
        header.addStretch()
        card_layout.addLayout(header)

        # Value text
        value_text = fmt_fn(lat, lon)
        value_lbl = QLabel(value_text)
        value_lbl.setWordWrap(True)
        value_lbl.setStyleSheet("font-size: 12px; color: #ccc; background: transparent;")
        card_layout.addWidget(value_lbl)

        # Copy button
        copy_btn = QPushButton("复制")
        copy_btn.setFixedWidth(52)
        copy_btn.setFixedHeight(22)
        copy_btn.setStyleSheet(
            "font-size: 11px; padding: 0 4px;"
        )
        copy_btn.clicked.connect(lambda checked, v=value_text, b=copy_btn: self._copy_value(v, b))
        card_layout.addWidget(copy_btn, alignment=Qt.AlignmentFlag.AlignRight)

        return card

    def _copy_value(self, text: str, btn: QPushButton) -> None:
        QApplication.clipboard().setText(text)
        btn.setText("✓")
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(1500, lambda: btn.setText("复制"))

    # ── Map ───────────────────────────────────────────────────────────────────

    def _on_map_toggle(self, checked: bool) -> None:
        self._map_open = checked
        self._map_toggle_btn.setText("关闭地图" if checked else "打开交互地图")
        self._map_container.setVisible(checked)
        self._map_confirm_container.setVisible(checked)

        if checked:
            self._ensure_web_view()
            # Move to parsed coords if available
            if self._parsed and self._web_view:
                gcj = wgs84_to_gcj02(self._parsed["lon"], self._parsed["lat"])
                self._call_map_js(
                    f"moveTo({gcj['lon']}, {gcj['lat']})"
                )
        else:
            self._map_selected_wgs = None
            self._map_confirm_btn.setEnabled(False)
            self._map_coord_label.setText("")

    def _ensure_web_view(self) -> None:
        """Lazily create QWebEngineView (deferred to avoid startup overhead).

        NOTE: QWebEngineView requires a real display; in offscreen/CI mode this
        may fail.  The try/except below degrades gracefully to a placeholder label.
        """
        if self._web_view is not None:
            return
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            from PyQt6.QtWebEngineCore import QWebEnginePage
            from PyQt6.QtCore import QObject, pyqtSlot as Slot

            # Build the web view
            wv = QWebEngineView()
            wv.setMinimumHeight(400)
            wv.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )

            # Inject JS bridge for marker-move callbacks
            class _Bridge(QObject):
                def __init__(self, view_ref: "CoordsView"):
                    super().__init__()
                    self._view = view_ref

                @pyqtSlot(str)
                def onMarkerMoved(self, payload: str) -> None:  # noqa: N802
                    try:
                        data = json.loads(payload)
                        self._view._map_selected_wgs = data
                        self._view._map_confirm_btn.setEnabled(True)
                        lat = data.get("lat", 0)
                        lon = data.get("lon", 0)
                        self._view._map_coord_label.setText(
                            f"WGS-84: {lat:.6f}, {lon:.6f}"
                        )
                    except Exception:
                        pass

            self._bridge = _Bridge(self)
            wv.page().setWebChannel(None)  # reset, we use runJavaScript bridge

            # Inject bridge via QWebChannel
            from PyQt6.QtWebChannel import QWebChannel
            channel = QWebChannel(wv.page())
            channel.registerObject("qt_bridge", self._bridge)
            wv.page().setWebChannel(channel)

            # Inject QWebChannel JS + map HTML
            wv.setHtml(
                _inject_webchannel_shim(_MAP_HTML),
                QUrl("https://map.local/")
            )

            self._map_container.layout().addWidget(wv)
            self._web_view = wv

        except Exception as exc:  # noqa: BLE001  # WebEngine unavailable (offscreen/CI)
            fallback = QLabel(
                f"交互地图不可用（QWebEngineView 加载失败）\n{exc}"
            )
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fallback.setStyleSheet("color: #888; background: transparent;")
            self._map_container.layout().addWidget(fallback)
            self._web_view = fallback  # sentinel so we don't retry

    def _call_map_js(self, js: str) -> None:
        """Run JavaScript in the map WebView (no-op if WebView unavailable)."""
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            if isinstance(self._web_view, QWebEngineView):
                self._web_view.page().runJavaScript(js)
        except Exception:
            pass

    def _on_place_search(self) -> None:
        q = self._place_input.text().strip()
        if not q:
            return
        if not self._map_open:
            self._map_toggle_btn.setChecked(True)
            self._on_map_toggle(True)
        self._call_map_js(f"doPlaceSearch({json.dumps(q)})")

    def _on_map_confirm(self) -> None:
        """Confirm selected map point → fill input field with DMS string."""
        sel = self._map_selected_wgs
        if not sel or not is_valid(sel.get("lat", 0), sel.get("lon", 0)):
            return
        from app.utils.coord_utils import to_dms
        dms_str = to_dms(sel["lat"], sel["lon"])
        self._input_edit.setText(dms_str)  # triggers _on_input_changed


# ── Helpers ───────────────────────────────────────────────────────────────────

def _inject_webchannel_shim(html: str) -> str:
    """Inject qwebchannel.js shim + bridge setup before </body>."""
    shim = """
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script>
document.addEventListener('DOMContentLoaded', function() {
  if (typeof QWebChannel !== 'undefined') {
    new QWebChannel(qt.webChannelTransport, function(channel) {
      window.qt_bridge = channel.objects.qt_bridge;
    });
  }
});
</script>
"""
    return html.replace("</body>", shim + "</body>")
