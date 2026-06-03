"""coords_view.py — 坐标工具视图（忠实还原 web 版 renderCoordPage）。

结构完全对齐 prototype-photo-gui/app.js renderCoordPage / renderCoordMapModal
与 styles.css coord-* 类：

  coord-header + coord-subtitle
  coord-panel
    coord-input-wrap   (文本输入 + 📍地图按钮)
    coord-format-badge (ok / err 状态)
    coord-cs-tabs      (十进制 / 度分秒 / 度分 — 平扁按钮+激活下划线)
    coord-cs-cards     (WGS-84 / GCJ-02 / BD09 纵向卡片列表)
    coord-place-wrap   (地名搜索输入 + 按钮)
    coord-place-loading / coord-place-results (结果列表 + 填入)
    coord-struct-toggle  (▶ 结构化输入 DMS，可折叠)
    coord-struct-input   (纬度/经度度分秒字段)
  coord-batch-section
    coord-batch-toggle   (▶ 批量转换)
    coord-batch-body
      textarea  + 解析/示例
      controls  (输出格式 + 坐标系)
      table     (# / 输入 / 北纬 / 东经)
      actions   (复制 CSV / 下载)

地图：QWebEngineView 内嵌高德地图，作为模态覆盖层；offscreen 时降级为
       QLabel 占位符（try/except）。

Backend / coord_utils 不改。
"""
from __future__ import annotations

import csv
import io
import json
from typing import Optional, TYPE_CHECKING

from PyQt6.QtCore import Qt, QUrl, pyqtSlot
from PyQt6.QtGui import QClipboard
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.views.base_view import BaseView
from app.utils.coord_utils import (
    parse_detailed,
    to_dd_zh,
    to_dms_zh,
    to_ddm_zh,
    to_dms,
    wgs84_to_gcj02,
    wgs84_to_bd09,
    gcj02_to_wgs84,
    from_dms_fields,
    is_valid,
)

if TYPE_CHECKING:
    from app.app_context import AppContext

# ── 高德地图凭证（来源：app.js line 31） ──────────────────────────────────────
_AMAP_KEY = "f9b9d89f08a91d7320a879970a784043"
_AMAP_SECURITY_CODE = "8d7ff2ed7f5e5dfd9fe0ba97e3616aa6"

# ── Theme shorthands (mirrors CSS :root custom properties) ────────────────────
_C = {
    "bg": "#08161b",
    "panel": "#10242a",
    "panel2": "#0b2025",
    "text": "#eef3ef",
    "text_soft": "#cfe0db",
    "muted": "#87a2a1",
    "muted_dim": "#5f7d7a",
    "accent": "#29b9ab",
    "accent_hover": "#31d4c4",
    "danger": "#e66e63",
    "border": "rgba(145,182,181,0.10)",
    "border_med": "rgba(145,182,181,0.18)",
    "input_bg": "#0f2127",
    "input_border": "rgba(145,182,181,0.16)",
    "success_soft": "rgba(54,201,143,0.14)",
    "success": "#36c98f",
}

# ── 批量示例数据（镜像 app.js batchExamples） ──────────────────────────────────
_BATCH_EXAMPLES: dict[str, str] = {
    "mixed": "\n".join([
        "29.11492, 121.76421",
        "24.48921N 118.18432E",
        '29°06\'53.7"N 121°45\'51.2"E',
        "北纬 24.48921  东经 118.18432",
        "30.25902, 122.17243",
        "26.0745N 119.2965E",
        'N24°29\'21.1" E118°11\'03.6"',
        "24.4886, 118.1841",
    ]),
    "dd": "\n".join([
        "29.11492, 121.76421",
        "24.48921, 118.18432",
        "30.25902, 122.17243",
        "26.07450, 119.29650",
        "24.48860, 118.18410",
        "28.23456, 120.56789",
    ]),
    "dms": "\n".join([
        '29°06\'53.7"N 121°45\'51.2"E',
        '24°29\'21.1"N 118°11\'03.6"E',
        '30°15\'32.5"N 122°10\'20.7"E',
        '26°04\'28.2"N 119°17\'47.4"E',
        '28°14\'04.4"N 120°34\'04.4"E',
    ]),
    "cn": "\n".join([
        "北纬 24.48921  东经 118.18432",
        "北纬 29.11492  东经 121.76421",
        "北纬 30.25902  东经 122.17243",
        "北纬 26.07450  东经 119.29650",
        "北纬 28.23456  东经 120.56789",
    ]),
    "prefix": "\n".join([
        'N24°29\'21.1" E118°11\'03.6"',
        'N29°06\'53.7" E121°45\'51.2"',
        'N30°15\'32.5" E122°10\'20.7"',
        'N26°04\'28.2" E119°17\'47.4"',
        'N28°14\'04.4" E120°34\'04.4"',
    ]),
    "ddm": "\n".join([
        "29°06.895'N 121°45.853'E",
        "24°29.352'N 118°11.060'E",
        "30°15.542'N 122°10.345'E",
        "26°04.470'N 119°17.790'E",
        "28°14.073'N 120°34.073'E",
    ]),
}

# ── Inline GCJ-02 math (used inside JS map HTML) ──────────────────────────────
_MAP_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:#0c1c22; color:#ccc; font-family:system-ui,sans-serif; font-size:13px; }
  #map { width:100%; height:100vh; }
  #info { position:absolute; bottom:0; left:0; right:0;
          background:rgba(0,0,0,0.75); padding:6px 10px; z-index:200; }
  #coordDisplay { color:#e0e0e0; margin-bottom:2px; }
  #coordFormats { color:#aaa; font-size:12px; }
</style>
</head>
<body>
<div id="map"></div>
<div id="info">
  <div id="coordDisplay">点击地图或拖拽标记选点</div>
  <div id="coordFormats"></div>
</div>
<script>
window._AMapSecurityConfig = { securityJsCode: "{SECURITY_CODE}" };
</script>
<script src="https://webapi.amap.com/maps?v=2.0&key={AMAP_KEY}&plugin=AMap.Geocoder,AMap.PlaceSearch"
        onerror="document.getElementById('coordDisplay').textContent='高德地图加载失败（需网络连接）'">
</script>
<script>
var _map=null, _marker=null, _selectedWgs=null;

function initMap(lat,lon){
  if(!window.AMap){ document.getElementById('coordDisplay').textContent='高德地图加载失败'; return; }
  var center=(lat!==null&&lon!==null)?[lon,lat]:[121.76,29.11];
  _map=new AMap.Map('map',{zoom:12,center:center,resizeEnable:true});
  AMap.plugin(['AMap.ToolBar'],function(){ _map.addControl(new AMap.ToolBar({position:'RT'})); });
  _map.on('click',function(e){ setMarker(e.lnglat.getLng(),e.lnglat.getLat()); });
  if(lat!==null&&lon!==null) setMarker(lon,lat);
  setTimeout(function(){ if(_map) _map.resize(); },300);
}

function setMarker(gcjLon,gcjLat){
  if(_marker) _map.remove(_marker);
  _marker=new AMap.Marker({position:[gcjLon,gcjLat],draggable:true,cursor:'move'});
  _marker.on('dragend',function(e){ var p=e.target.getPosition(); updateDisplay(p.getLng(),p.getLat()); });
  _map.add(_marker);
  updateDisplay(gcjLon,gcjLat);
}

function updateDisplay(gcjLon,gcjLat){
  var wLon=gcjLon, wLat=gcjLat;
  for(var i=0;i<5;i++){ var g=wgs84ToGcj02(wLon,wLat); wLon+=gcjLon-g.lon; wLat+=gcjLat-g.lat; }
  _selectedWgs={lat:+wLat.toFixed(7),lon:+wLon.toFixed(7)};
  document.getElementById('coordDisplay').textContent=
    'WGS-84: '+_selectedWgs.lat.toFixed(6)+', '+_selectedWgs.lon.toFixed(6)+
    '  (GCJ-02: '+gcjLat.toFixed(6)+', '+gcjLon.toFixed(6)+')';
  document.getElementById('coordFormats').textContent=
    'DMS: '+toDMS(_selectedWgs.lat,_selectedWgs.lon)+
    '  |  DDM: '+toDDM(_selectedWgs.lat,_selectedWgs.lon);
  if(window.qt_bridge) window.qt_bridge.onMarkerMoved(JSON.stringify(_selectedWgs));
}

function doPlaceSearch(q){
  if(!q||!window.AMap) return;
  AMap.plugin('AMap.PlaceSearch',function(){
    var ps=new AMap.PlaceSearch({pageSize:5,pageIndex:1});
    ps.search(q,function(status,result){
      if(status==='complete'&&result.poiList&&result.poiList.pois.length>0){
        var poi=result.poiList.pois[0]; var loc=poi.location;
        _map.setCenter([loc.getLng(),loc.getLat()]); _map.setZoom(15);
        setMarker(loc.getLng(),loc.getLat());
      }
    });
  });
}

function moveTo(gcjLon,gcjLat){
  if(!_map) return;
  _map.setCenter([gcjLon,gcjLat]); _map.setZoom(14); setMarker(gcjLon,gcjLat);
}

var PI=Math.PI, A=6378245.0, EE=0.00669342162296594323;
function inChina(lon,lat){ return lon>=73.66&&lon<=135.05&&lat>=3.86&&lat<=53.55; }
function transLat(x,y){
  var r=-100+2*x+3*y+0.2*y*y+0.1*x*y+0.2*Math.sqrt(Math.abs(x));
  r+=(20*Math.sin(6*x*PI)+20*Math.sin(2*x*PI))*2/3;
  r+=(20*Math.sin(y*PI)+40*Math.sin(y/3*PI))*2/3;
  r+=(160*Math.sin(y/12*PI)+320*Math.sin(y*PI/30))*2/3;
  return r;
}
function transLon(x,y){
  var r=300+x+2*y+0.1*x*y+0.1*Math.sqrt(Math.abs(x));
  r+=(20*Math.sin(6*x*PI)+20*Math.sin(2*x*PI))*2/3;
  r+=(20*Math.sin(x*PI)+40*Math.sin(x/3*PI))*2/3;
  r+=(150*Math.sin(x/12*PI)+300*Math.sin(x/30*PI))*2/3;
  return r;
}
function wgs84ToGcj02(lon,lat){
  if(!inChina(lon,lat)) return {lon:lon,lat:lat};
  var dLat=transLat(lon-105,lat-35), dLon=transLon(lon-105,lat-35);
  var rad=lat/180*PI, magic=Math.sin(rad); magic=1-EE*magic*magic;
  var sq=Math.sqrt(magic);
  dLat=dLat*180/((A*(1-EE))/(magic*sq)*PI);
  dLon=dLon*180/((A/sq)/Math.cos(rad)*PI);
  return {lon:+(lon+dLon).toFixed(6),lat:+(lat+dLat).toFixed(6)};
}
function toDMS(lat,lon){
  function fmt(dd){ var a=Math.abs(dd),d=Math.floor(a),mf=(a-d)*60,m=Math.floor(mf),s=((mf-m)*60).toFixed(1); return d+'°'+m+"'"+s+'"'; }
  return fmt(lat)+(lat>=0?'N':'S')+' '+fmt(lon)+(lon>=0?'E':'W');
}
function toDDM(lat,lon){
  function fmt(dd){ var a=Math.abs(dd),d=Math.floor(a),m=((a-d)*60).toFixed(3); return d+'°'+m+"'"; }
  return fmt(lat)+(lat>=0?'N':'S')+' '+fmt(lon)+(lon>=0?'E':'W');
}
</script>
</body>
</html>
""".replace("{AMAP_KEY}", _AMAP_KEY).replace("{SECURITY_CODE}", _AMAP_SECURITY_CODE)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _inject_webchannel_shim(html: str) -> str:
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


def _btn(text: str, object_name: str = "") -> QPushButton:
    b = QPushButton(text)
    if object_name:
        b.setObjectName(object_name)
    return b


def _label(text: str, *, object_name: str = "", style: str = "") -> QLabel:
    lb = QLabel(text)
    if object_name:
        lb.setObjectName(object_name)
    if style:
        lb.setStyleSheet(style)
    return lb


# ── CoordsView ─────────────────────────────────────────────────────────────────

class CoordsView(BaseView):
    """坐标工具视图 — 忠实还原 web renderCoordPage。

    布局（从上到下）：
      头部（标题 + 副标题）
      输入面板（单坐标：输入框 / 徽章 / CS 卡片 / 地名搜索 / 结构化输入）
      批量转换区（可折叠）
      地图模态（覆盖层，按地图按钮触发）
    """

    view_id = "coords"
    nav_title = "坐标工具"
    nav_icon = "📍"

    _CS_TABS = [("dd", "十进制"), ("dms", "度分秒"), ("ddm", "度分")]

    # ── 状态 ──────────────────────────────────────────────────────────────────

    def __init__(self, ctx: "AppContext") -> None:
        # state (mirrors state.coordTool / state.batchCoord in web)
        self._input_val: str = ""
        self._parsed: Optional[dict] = None
        self._cs_tab: str = "dd"
        self._place_loading: bool = False
        self._place_results: list[dict] = []
        self._show_structured: bool = False
        self._show_batch: bool = False

        # batch state
        self._batch_raw: str = ""
        self._batch_rows: list[dict] = []
        self._batch_fmt: str = "dd"    # "dd" / "dms" / "ddm"
        self._batch_cs: str = "wgs84"  # "wgs84" / "gcj02" / "bd09"

        # map state
        self._map_open: bool = False
        self._map_selected_wgs: Optional[dict] = None
        self._web_view = None   # lazy QWebEngineView

        super().__init__(ctx)

    # ── BaseView ─────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        # Outer scroll area so long pages scroll
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(self._scroll)

        # Content container
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(20, 20, 20, 20)
        self._content_layout.setSpacing(0)
        self._scroll.setWidget(self._content)

        self._build_header()
        self._build_panel()
        self._build_batch_section()

        self._content_layout.addStretch()

        # Map modal placeholder (actual overlay appended to top-level widget)
        self._map_overlay: Optional[QWidget] = None

    def on_activate(self) -> None:
        pass

    # ── Header ────────────────────────────────────────────────────────────────

    def _build_header(self) -> None:
        header = QWidget()
        header.setObjectName("CoordHeader")
        header.setStyleSheet("QWidget#CoordHeader { margin-bottom: 16px; }")
        lay = QVBoxLayout(header)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        h2 = QLabel("坐标工具")
        h2.setStyleSheet(
            f"font-size: 19px; font-weight: 600; color: {_C['text']}; background: transparent;"
        )
        lay.addWidget(h2)

        sub = QLabel(
            "输入或粘贴坐标，支持 DD / DMS / DDM / ISO 6709 格式；"
            "也可地名搜索或地图选点。输出统一为 WGS-84。"
        )
        sub.setWordWrap(True)
        sub.setStyleSheet(
            f"font-size: 12px; color: {_C['muted']}; background: transparent; line-height: 1.5;"
        )
        lay.addWidget(sub)

        self._content_layout.addWidget(header)

    # ── Single-coord panel ────────────────────────────────────────────────────

    def _build_panel(self) -> None:
        panel = QWidget()
        panel.setObjectName("CoordPanel")
        panel.setStyleSheet(
            f"QWidget#CoordPanel {{ background: {_C['panel']};"
            f" border: 1px solid {_C['border_med']}; border-radius: 10px;"
            f" padding: 16px; margin-bottom: 12px; }}"
        )
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(0)

        # ── Input row (coord-input-wrap) ──────────────────────────────────────
        input_row = QHBoxLayout()
        input_row.setSpacing(6)

        self._input_edit = QLineEdit()
        self._input_edit.setPlaceholderText(
            "粘贴或输入坐标，如 29.11492N 121.76421E 或 29°06'53.7\"N 121°45'51.2\"E"
        )
        self._input_edit.setObjectName("CoordInput")
        self._input_edit.textChanged.connect(self._on_input_changed)
        input_row.addWidget(self._input_edit, 1)

        map_btn = QPushButton("📍")
        map_btn.setObjectName("CoordMapBtn")
        map_btn.setToolTip("打开地图选点")
        map_btn.setFixedSize(36, 34)
        map_btn.setStyleSheet(
            f"QPushButton#CoordMapBtn {{ background: {_C['panel2']};"
            f" border: 1px solid {_C['border_med']}; border-radius: 6px;"
            f" font-size: 16px; }}"
            f"QPushButton#CoordMapBtn:hover {{ border-color: {_C['accent']}; }}"
        )
        map_btn.clicked.connect(self._on_open_map)
        input_row.addWidget(map_btn)

        lay.addLayout(input_row)
        lay.addSpacing(8)

        # ── Format badge (coord-format-badge) ─────────────────────────────────
        self._badge = QLabel("")
        self._badge.setObjectName("CoordBadge")
        self._badge.setWordWrap(True)
        self._badge.setVisible(False)
        self._badge.setStyleSheet(
            "QLabel#CoordBadge { padding: 4px 8px; border-radius: 4px; font-size: 12px; }"
        )
        lay.addWidget(self._badge)

        # ── CS tab bar + CS cards container ───────────────────────────────────
        self._cs_section = QWidget()
        self._cs_section_lay = QVBoxLayout(self._cs_section)
        self._cs_section_lay.setContentsMargins(0, 0, 0, 0)
        self._cs_section_lay.setSpacing(0)

        # Tab bar (coord-cs-tabs)
        tab_row = QHBoxLayout()
        tab_row.setSpacing(0)
        tab_row.setContentsMargins(0, 0, 0, 0)
        self._cs_tab_btns: dict[str, QPushButton] = {}
        for key, label in self._CS_TABS:
            btn = QPushButton(label)
            btn.setObjectName(f"CsTab_{key}")
            btn.setProperty("cs_key", key)
            btn.setCheckable(True)
            btn.setChecked(key == self._cs_tab)
            btn.clicked.connect(lambda checked, k=key: self._on_cs_tab(k))
            self._cs_tab_btns[key] = btn
            tab_row.addWidget(btn)
        tab_row.addStretch()
        self._cs_section_lay.addLayout(tab_row)
        self._cs_section_lay.addSpacing(6)

        # CS cards container
        self._cs_cards_widget = QWidget()
        self._cs_cards_lay = QVBoxLayout(self._cs_cards_widget)
        self._cs_cards_lay.setContentsMargins(0, 0, 0, 0)
        self._cs_cards_lay.setSpacing(6)
        self._cs_section_lay.addWidget(self._cs_cards_widget)

        self._cs_section.setVisible(False)
        lay.addWidget(self._cs_section)
        lay.addSpacing(12)

        # ── Place search (coord-place-wrap) ────────────────────────────────────
        place_row = QHBoxLayout()
        place_row.setSpacing(6)
        self._place_input = QLineEdit()
        self._place_input.setPlaceholderText("输入地名搜索坐标，如 三门湾、北京、舟山")
        place_row.addWidget(self._place_input, 1)
        self._place_btn = QPushButton("搜索地名")
        self._place_btn.setObjectName("Primary")
        self._place_btn.setFixedWidth(80)
        self._place_btn.clicked.connect(self._on_place_search)
        self._place_input.returnPressed.connect(self._on_place_search)
        place_row.addWidget(self._place_btn)
        lay.addLayout(place_row)

        # Place loading label
        self._place_loading_lbl = QLabel("搜索中...")
        self._place_loading_lbl.setStyleSheet(
            f"font-size: 12px; color: {_C['muted']}; background: transparent; padding: 6px 0;"
        )
        self._place_loading_lbl.setVisible(False)
        lay.addWidget(self._place_loading_lbl)

        # Place results container
        self._place_results_widget = QWidget()
        self._place_results_lay = QVBoxLayout(self._place_results_widget)
        self._place_results_lay.setContentsMargins(0, 4, 0, 0)
        self._place_results_lay.setSpacing(4)
        self._place_results_widget.setVisible(False)
        lay.addWidget(self._place_results_widget)

        lay.addSpacing(8)

        # ── Structured DMS toggle (coord-struct-toggle) ────────────────────────
        self._struct_toggle = QPushButton("▶ 结构化输入 (DMS)")
        self._struct_toggle.setObjectName("Ghost")
        self._struct_toggle.setStyleSheet(
            f"QPushButton {{ background: none; border: none; color: {_C['muted']};"
            f" font-size: 12px; font-weight: 500; text-align: left; padding: 4px 0; }}"
            f"QPushButton:hover {{ color: {_C['accent']}; }}"
        )
        self._struct_toggle.clicked.connect(self._on_struct_toggle)
        lay.addWidget(self._struct_toggle)

        # Structured DMS inputs (hidden by default)
        self._struct_widget = QWidget()
        self._struct_lay = QVBoxLayout(self._struct_widget)
        self._struct_lay.setContentsMargins(0, 6, 0, 0)
        self._struct_lay.setSpacing(6)
        self._struct_widget.setVisible(False)

        # Lat row: degrees, minutes, seconds, direction
        self._struct_lat_d, self._struct_lat_m, self._struct_lat_s, self._struct_lat_dir = (
            self._make_dms_row("纬度", "N", self._struct_lay)
        )
        # Lon row
        self._struct_lon_d, self._struct_lon_m, self._struct_lon_s, self._struct_lon_dir = (
            self._make_dms_row("经度", "E", self._struct_lay)
        )
        # Connect sync
        for field in (self._struct_lat_d, self._struct_lat_m, self._struct_lat_s):
            field.textChanged.connect(self._on_struct_changed)
        self._struct_lat_dir.currentTextChanged.connect(self._on_struct_changed)
        for field in (self._struct_lon_d, self._struct_lon_m, self._struct_lon_s):
            field.textChanged.connect(self._on_struct_changed)
        self._struct_lon_dir.currentTextChanged.connect(self._on_struct_changed)

        lay.addWidget(self._struct_widget)

        self._content_layout.addWidget(panel)

        # Apply tab button styles
        self._refresh_cs_tab_styles()

    def _make_dms_row(
        self,
        label_text: str,
        default_dir: str,
        parent_lay: QVBoxLayout,
    ) -> tuple:
        """Build a DMS input row (label, d°, m′, s″, direction select)."""
        row_w = QWidget()
        row_lay = QHBoxLayout(row_w)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(4)

        lbl = QLabel(label_text)
        lbl.setFixedWidth(36)
        lbl.setStyleSheet(f"font-size: 12px; color: {_C['muted']}; background: transparent;")
        row_lay.addWidget(lbl)

        def _num_edit(placeholder: str, max_val: str) -> QLineEdit:
            e = QLineEdit()
            e.setPlaceholderText(placeholder)
            e.setFixedWidth(54)
            return e

        d_edit = _num_edit("度", "180")
        row_lay.addWidget(d_edit)
        row_lay.addWidget(QLabel("°"))

        m_edit = _num_edit("分", "59")
        row_lay.addWidget(m_edit)
        row_lay.addWidget(QLabel("′"))

        s_edit = _num_edit("秒", "59.9")
        row_lay.addWidget(s_edit)
        row_lay.addWidget(QLabel("″"))

        if label_text == "纬度":
            opts = ["N", "S"]
        else:
            opts = ["E", "W"]
        dir_sel = QComboBox()
        dir_sel.addItems(opts)
        dir_sel.setCurrentText(default_dir)
        dir_sel.setFixedWidth(54)
        row_lay.addWidget(dir_sel)
        row_lay.addStretch()

        parent_lay.addWidget(row_w)
        return d_edit, m_edit, s_edit, dir_sel

    # ── Batch section ─────────────────────────────────────────────────────────

    def _build_batch_section(self) -> None:
        batch_wrap = QWidget()
        batch_wrap.setObjectName("CoordBatchSection")
        batch_lay = QVBoxLayout(batch_wrap)
        batch_lay.setContentsMargins(0, 0, 0, 0)
        batch_lay.setSpacing(0)

        # Toggle button (coord-batch-toggle)
        self._batch_toggle = QPushButton("▶ 批量转换")
        self._batch_toggle.setStyleSheet(
            f"QPushButton {{ background: none; border: 1px solid {_C['border_med']};"
            f" border-radius: 6px; padding: 6px 10px; font-size: 12px;"
            f" color: {_C['text']}; font-weight: 500; text-align: left; }}"
            f"QPushButton:hover {{ border-color: {_C['accent']}; color: {_C['accent']}; }}"
        )
        self._batch_toggle.clicked.connect(self._on_batch_toggle)
        batch_lay.addWidget(self._batch_toggle)

        # Body (hidden by default)
        self._batch_body = QWidget()
        self._batch_body_lay = QVBoxLayout(self._batch_body)
        self._batch_body_lay.setContentsMargins(0, 10, 0, 0)
        self._batch_body_lay.setSpacing(8)
        self._batch_body.setVisible(False)

        # Textarea
        self._batch_textarea = QPlainTextEdit()
        self._batch_textarea.setPlaceholderText(
            "粘贴多行坐标（每行一条），支持混合格式：\n"
            "29.11492N 121.76421E\n"
            "29°06'53.7\"N 121°45'51.2\"E\n"
            "北纬 24.48921  东经 118.18432"
        )
        self._batch_textarea.setMinimumHeight(100)
        self._batch_textarea.setMaximumHeight(180)
        font = self._batch_textarea.font()
        font.setFamily("Consolas, monospace")
        font.setPointSize(9)
        self._batch_textarea.setFont(font)
        self._batch_body_lay.addWidget(self._batch_textarea)

        # Parse button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        parse_btn = QPushButton("解析")
        parse_btn.setObjectName("Primary")
        parse_btn.setFixedWidth(72)
        parse_btn.clicked.connect(self._on_batch_parse)
        btn_row.addWidget(parse_btn)

        self._example_combo = QComboBox()
        self._example_combo.addItem("示例数据…")
        for key, label in [
            ("mixed", "混合格式（DD / DMS / 中文）"),
            ("dd", "纯十进制度 DD"),
            ("dms", "纯度分秒 DMS"),
            ("cn", "纯中文格式"),
            ("prefix", "方位前置 N24°…"),
            ("ddm", "纯度分 DDM"),
        ]:
            self._example_combo.addItem(label, userData=key)
        self._example_combo.currentIndexChanged.connect(self._on_example_select)
        btn_row.addWidget(self._example_combo)
        btn_row.addStretch()
        self._batch_body_lay.addLayout(btn_row)

        # Controls (output format + CS) — hidden until rows present
        self._batch_controls = QWidget()
        ctrl_lay = QVBoxLayout(self._batch_controls)
        ctrl_lay.setContentsMargins(0, 0, 0, 0)
        ctrl_lay.setSpacing(6)

        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(6)
        fmt_row.addWidget(
            QLabel("输出格式：",
                   styleSheet=f"font-size: 12px; color: {_C['muted']}; background: transparent;")
        )
        self._batch_fmt_btns: dict[str, QPushButton] = {}
        for key, lbl in [("dd", "DD"), ("dms", "DMS"), ("ddm", "DDM")]:
            b = QPushButton(lbl)
            b.setCheckable(True)
            b.setChecked(key == self._batch_fmt)
            b.setProperty("batch_fmt_key", key)
            b.clicked.connect(lambda _, k=key: self._on_batch_fmt(k))
            self._batch_fmt_btns[key] = b
            fmt_row.addWidget(b)
        fmt_row.addStretch()
        ctrl_lay.addLayout(fmt_row)

        cs_row = QHBoxLayout()
        cs_row.setSpacing(6)
        cs_row.addWidget(
            QLabel("输出坐标系：",
                   styleSheet=f"font-size: 12px; color: {_C['muted']}; background: transparent;")
        )
        self._batch_cs_btns: dict[str, QPushButton] = {}
        for key, lbl in [("wgs84", "WGS-84"), ("gcj02", "GCJ-02"), ("bd09", "BD09")]:
            b = QPushButton(lbl)
            b.setCheckable(True)
            b.setChecked(key == self._batch_cs)
            b.setProperty("batch_cs_key", key)
            b.clicked.connect(lambda _, k=key: self._on_batch_cs(k))
            self._batch_cs_btns[key] = b
            cs_row.addWidget(b)
        cs_row.addStretch()
        ctrl_lay.addLayout(cs_row)

        self._batch_controls.setVisible(False)
        self._batch_body_lay.addWidget(self._batch_controls)

        # Table
        self._batch_table = QTableWidget()
        self._batch_table.setColumnCount(4)
        self._batch_table.setHorizontalHeaderLabels(["#", "输入", "北纬", "东经"])
        self._batch_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._batch_table.setAlternatingRowColors(True)
        self._batch_table.verticalHeader().setVisible(False)
        self._batch_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._batch_table.horizontalHeader().setStretchLastSection(True)
        self._batch_table.setMinimumHeight(160)
        self._batch_table.setMaximumHeight(320)
        self._batch_table.setVisible(False)
        self._batch_body_lay.addWidget(self._batch_table)

        # Action buttons (CSV)
        self._batch_actions = QWidget()
        act_lay = QHBoxLayout(self._batch_actions)
        act_lay.setContentsMargins(0, 0, 0, 0)
        act_lay.setSpacing(6)
        self._csv_copy_btn = QPushButton("复制 CSV")
        self._csv_copy_btn.clicked.connect(self._on_copy_csv)
        act_lay.addWidget(self._csv_copy_btn)
        self._csv_dl_btn = QPushButton("下载 .csv")
        self._csv_dl_btn.clicked.connect(self._on_download_csv)
        act_lay.addWidget(self._csv_dl_btn)
        act_lay.addStretch()
        self._batch_actions.setVisible(False)
        self._batch_body_lay.addWidget(self._batch_actions)

        batch_lay.addWidget(self._batch_body)
        self._content_layout.addWidget(batch_wrap)

    # ── Input ─────────────────────────────────────────────────────────────────

    def _on_input_changed(self, text: str) -> None:
        self._input_val = text
        if text.strip():
            self._parsed = parse_detailed(text) or None
        else:
            self._parsed = None
        self._update_badge()
        self._update_cs_section()
        # Propagate to map if open
        if self._map_open and self._parsed and self._web_view:
            gcj = wgs84_to_gcj02(self._parsed["lon"], self._parsed["lat"])
            self._call_map_js(f"moveTo({gcj['lon']}, {gcj['lat']})")

    # ── Badge ─────────────────────────────────────────────────────────────────

    def _update_badge(self) -> None:
        if not self._input_val.strip():
            self._badge.setVisible(False)
            return
        self._badge.setVisible(True)
        if self._parsed:
            p = self._parsed
            self._badge.setText(
                f"✓ {p['format_label']} — lat {p['lat']:.6f}, lon {p['lon']:.6f}"
            )
            self._badge.setStyleSheet(
                f"QLabel#CoordBadge {{ padding: 4px 8px; border-radius: 4px; font-size: 12px;"
                f" background: rgba(41,185,171,0.12); color: {_C['accent']};"
                f" border: 1px solid rgba(41,185,171,0.25); }}"
            )
        else:
            self._badge.setText("无法识别坐标格式")
            self._badge.setStyleSheet(
                f"QLabel#CoordBadge {{ padding: 4px 8px; border-radius: 4px; font-size: 12px;"
                f" background: rgba(230,110,99,0.12); color: {_C['danger']};"
                f" border: 1px solid rgba(230,110,99,0.25); }}"
            )

    # ── CS tab bar & cards ────────────────────────────────────────────────────

    def _on_cs_tab(self, key: str) -> None:
        self._cs_tab = key
        self._refresh_cs_tab_styles()
        self._rebuild_cs_cards()

    def _refresh_cs_tab_styles(self) -> None:
        for k, b in self._cs_tab_btns.items():
            b.setChecked(k == self._cs_tab)
            if k == self._cs_tab:
                b.setStyleSheet(
                    f"QPushButton {{ background: none; border: none;"
                    f" border-bottom: 2px solid {_C['accent']}; padding: 6px 14px 4px;"
                    f" font-size: 12px; font-weight: 600; color: {_C['accent']}; }}"
                )
            else:
                b.setStyleSheet(
                    f"QPushButton {{ background: none; border: none;"
                    f" border-bottom: 2px solid transparent; padding: 6px 14px 4px;"
                    f" font-size: 12px; font-weight: 500; color: {_C['muted']}; }}"
                    f"QPushButton:hover {{ color: {_C['text']}; }}"
                )

    def _update_cs_section(self) -> None:
        if not self._parsed:
            self._cs_section.setVisible(False)
            return
        self._cs_section.setVisible(True)
        self._rebuild_cs_cards()

    def _rebuild_cs_cards(self) -> None:
        # Clear existing cards
        while self._cs_cards_lay.count():
            item = self._cs_cards_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._parsed:
            return

        p = self._parsed
        lat, lon = p["lat"], p["lon"]
        gcj = wgs84_to_gcj02(lon, lat)
        bd = wgs84_to_bd09(lon, lat)

        systems = [
            ("WGS-84", "国际通用", lat, lon),
            ("GCJ-02", "国测局", gcj["lat"], gcj["lon"]),
            ("BD09", "百度", bd["lat"], bd["lon"]),
        ]
        fmt_fn = {"dd": to_dd_zh, "dms": to_dms_zh, "ddm": to_ddm_zh}[self._cs_tab]

        for name, sub, clat, clon in systems:
            card = self._make_cs_card(name, sub, clat, clon, fmt_fn)
            self._cs_cards_lay.addWidget(card)

    def _make_cs_card(self, name: str, sub: str, lat: float, lon: float, fmt_fn) -> QWidget:
        """coord-cs-card: horizontal row with label-col | value | copy-btn."""
        card = QFrame()
        card.setObjectName("CoordCsCard")
        card.setStyleSheet(
            f"QFrame#CoordCsCard {{ background: {_C['panel2']};"
            f" border: 1px solid {_C['border_med']}; border-radius: 8px; }}"
            f"QFrame#CoordCsCard:hover {{ border-color: {_C['accent']}; }}"
        )
        card_lay = QHBoxLayout(card)
        card_lay.setContentsMargins(12, 10, 12, 10)
        card_lay.setSpacing(10)

        # Label column (coord-cs-card-label)
        lbl_col = QWidget()
        lbl_col.setStyleSheet("background: transparent;")
        lbl_lay = QHBoxLayout(lbl_col)
        lbl_lay.setContentsMargins(0, 0, 0, 0)
        lbl_lay.setSpacing(6)
        name_lbl = QLabel(name)
        name_lbl.setStyleSheet(
            f"font-weight: 600; font-size: 13px; color: {_C['text']}; background: transparent;"
        )
        sub_lbl = QLabel(sub)
        sub_lbl.setStyleSheet(
            f"font-size: 11px; color: {_C['muted']}; background: transparent;"
        )
        lbl_lay.addWidget(name_lbl)
        lbl_lay.addWidget(sub_lbl)
        lbl_col.setFixedWidth(130)
        card_lay.addWidget(lbl_col)

        # Value (coord-cs-card-value)
        value_text = fmt_fn(lat, lon)
        value_lbl = QLabel(value_text)
        value_lbl.setStyleSheet(
            f"font-family: 'Consolas', 'JetBrains Mono', monospace;"
            f" font-size: 12px; color: {_C['text']}; background: transparent;"
        )
        value_lbl.setWordWrap(True)
        card_lay.addWidget(value_lbl, 1)

        # Copy button (coord-copy-btn)
        copy_btn = QPushButton("复制")
        copy_btn.setFixedWidth(48)
        copy_btn.setFixedHeight(26)
        copy_btn.setStyleSheet(
            f"QPushButton {{ background: none; border: 1px solid {_C['border_med']};"
            f" border-radius: 4px; font-size: 11px; color: {_C['muted']}; padding: 0; }}"
            f"QPushButton:hover {{ border-color: {_C['accent']}; color: {_C['accent']}; }}"
        )
        copy_btn.clicked.connect(
            lambda _, v=value_text, b=copy_btn: self._copy_value(v, b)
        )
        card_lay.addWidget(copy_btn)

        return card

    def _copy_value(self, text: str, btn: QPushButton) -> None:
        QApplication.clipboard().setText(text)
        orig = btn.text()
        btn.setText("✓")
        btn.setStyleSheet(
            f"QPushButton {{ background: none; border: 1px solid {_C['accent']};"
            f" border-radius: 4px; font-size: 11px; color: {_C['accent']}; padding: 0; }}"
        )
        from PyQt6.QtCore import QTimer
        def _restore():
            btn.setText(orig)
            btn.setStyleSheet(
                f"QPushButton {{ background: none; border: 1px solid {_C['border_med']};"
                f" border-radius: 4px; font-size: 11px; color: {_C['muted']}; padding: 0; }}"
                f"QPushButton:hover {{ border-color: {_C['accent']}; color: {_C['accent']}; }}"
            )
        QTimer.singleShot(1500, _restore)

    # ── Place search ──────────────────────────────────────────────────────────

    def _on_place_search(self) -> None:
        q = self._place_input.text().strip()
        if not q:
            return
        self._place_loading = True
        self._place_results = []
        self._refresh_place_ui()
        # Try via AMap if map is open / web view available; otherwise use
        # Nominatim HTTP (no API key required) as fallback.
        if self._map_open and self._web_view:
            self._call_map_js(f"doPlaceSearch({json.dumps(q)})")
            # Results will come back via map; hide loading after delay
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(3000, lambda: self._set_place_loading(False))
        else:
            # Nominatim geocoder (lightweight, no key)
            from PyQt6.QtCore import QThread, pyqtSignal, QObject

            class _Geocoder(QObject):
                done = pyqtSignal(list)

                def __init__(self, query: str):
                    super().__init__()
                    self._q = query

                def run(self) -> None:
                    import urllib.request
                    import urllib.parse
                    try:
                        url = (
                            "https://nominatim.openstreetmap.org/search?"
                            + urllib.parse.urlencode({
                                "q": self._q,
                                "format": "json",
                                "limit": 5,
                                "accept-language": "zh",
                            })
                        )
                        req = urllib.request.Request(url, headers={"User-Agent": "photo-platform-gui/1.0"})
                        with urllib.request.urlopen(req, timeout=5) as resp:
                            data = json.loads(resp.read().decode())
                    except Exception:
                        data = []
                    results = []
                    for item in data:
                        try:
                            lat = float(item["lat"])
                            lon = float(item["lon"])
                            results.append({"name": item.get("display_name", "?")[:60], "wgs": {"lat": lat, "lon": lon}})
                        except Exception:
                            pass
                    self.done.emit(results)

            # Use a thread to avoid blocking UI
            thread = QThread(self)
            geo = _Geocoder(q)
            geo.moveToThread(thread)

            def _on_done(results: list) -> None:
                self._place_loading = False
                self._place_results = results
                self._refresh_place_ui()
                thread.quit()
                thread.wait()

            geo.done.connect(_on_done)
            thread.started.connect(geo.run)
            thread.start()

    def _set_place_loading(self, loading: bool) -> None:
        self._place_loading = loading
        self._refresh_place_ui()

    def _refresh_place_ui(self) -> None:
        self._place_loading_lbl.setVisible(self._place_loading)

        # Rebuild results
        while self._place_results_lay.count():
            item = self._place_results_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._place_results:
            self._place_results_widget.setVisible(False)
            return

        self._place_results_widget.setVisible(True)
        for r in self._place_results:
            item_w = QWidget()
            item_lay = QHBoxLayout(item_w)
            item_lay.setContentsMargins(8, 6, 8, 6)
            item_lay.setSpacing(8)
            item_w.setStyleSheet(
                f"QWidget {{ background: {_C['panel2']}; border: 1px solid {_C['border_med']};"
                f" border-radius: 6px; }}"
                f"QWidget:hover {{ border-color: {_C['accent']}; }}"
            )

            name_lbl = QLabel(r["name"])
            name_lbl.setStyleSheet(
                f"font-size: 12px; color: {_C['text']}; background: transparent;"
            )
            name_lbl.setWordWrap(True)
            item_lay.addWidget(name_lbl, 1)

            coord_lbl = QLabel(
                f"{r['wgs']['lat']:.5f}, {r['wgs']['lon']:.5f}"
            )
            coord_lbl.setStyleSheet(
                f"font-family: monospace; font-size: 11px; color: {_C['muted']}; background: transparent;"
            )
            item_lay.addWidget(coord_lbl)

            apply_btn = QPushButton("填入")
            apply_btn.setFixedWidth(46)
            apply_btn.setFixedHeight(24)
            apply_btn.setStyleSheet(
                f"QPushButton {{ background: none; border: 1px solid {_C['border_med']};"
                f" border-radius: 4px; font-size: 11px; color: {_C['muted']}; padding: 0; }}"
                f"QPushButton:hover {{ background: {_C['accent']}; color: {_C['bg']}; }}"
            )
            wgs = r["wgs"]
            apply_btn.clicked.connect(
                lambda _, lat=wgs["lat"], lon=wgs["lon"]: self._apply_place(lat, lon)
            )
            item_lay.addWidget(apply_btn)

            self._place_results_lay.addWidget(item_w)

    def _apply_place(self, lat: float, lon: float) -> None:
        self._input_edit.setText(f"{lat:.6f}, {lon:.6f}")

    # ── Structured DMS ────────────────────────────────────────────────────────

    def _on_struct_toggle(self) -> None:
        self._show_structured = not self._show_structured
        arrow = "▼" if self._show_structured else "▶"
        self._struct_toggle.setText(f"{arrow} 结构化输入 (DMS)")
        self._struct_widget.setVisible(self._show_structured)
        if self._show_structured and self._parsed:
            self._populate_struct_fields()

    def _populate_struct_fields(self) -> None:
        if not self._parsed:
            return
        p = self._parsed
        # Fill from parsed dms components
        dms = p.get("dms", {})
        ld = dms.get("lat", {})
        lo = dms.get("lon", {})
        self._struct_lat_d.setText(str(ld.get("d", "")))
        self._struct_lat_m.setText(str(ld.get("m", "")))
        self._struct_lat_s.setText(str(ld.get("s", "")))
        self._struct_lat_dir.setCurrentText(p.get("lat_direction", "N"))
        self._struct_lon_d.setText(str(lo.get("d", "")))
        self._struct_lon_m.setText(str(lo.get("m", "")))
        self._struct_lon_s.setText(str(lo.get("s", "")))
        self._struct_lon_dir.setCurrentText(p.get("lon_direction", "E"))

    def _on_struct_changed(self) -> None:
        """Sync structured DMS fields → main input."""
        try:
            lat_d = float(self._struct_lat_d.text() or "0")
            lat_m = float(self._struct_lat_m.text() or "0")
            lat_s = float(self._struct_lat_s.text() or "0")
            lat_dir = self._struct_lat_dir.currentText()
            lon_d = float(self._struct_lon_d.text() or "0")
            lon_m = float(self._struct_lon_m.text() or "0")
            lon_s = float(self._struct_lon_s.text() or "0")
            lon_dir = self._struct_lon_dir.currentText()
        except ValueError:
            return
        lat_val = from_dms_fields(lat_d, lat_m, lat_s, lat_dir)
        lon_val = from_dms_fields(lon_d, lon_m, lon_s, lon_dir)
        if not is_valid(lat_val, lon_val):
            return
        dms_str = to_dms(lat_val, lon_val)
        # Block signal loop
        self._input_edit.blockSignals(True)
        self._input_edit.setText(dms_str)
        self._input_edit.blockSignals(False)
        self._input_val = dms_str
        self._parsed = parse_detailed(dms_str) or None
        self._update_badge()
        self._update_cs_section()

    # ── Batch conversion ──────────────────────────────────────────────────────

    def _on_batch_toggle(self) -> None:
        self._show_batch = not self._show_batch
        arrow = "▼" if self._show_batch else "▶"
        self._batch_toggle.setText(f"{arrow} 批量转换")
        self._batch_body.setVisible(self._show_batch)

    def _on_batch_parse(self) -> None:
        raw = self._batch_textarea.toPlainText()
        self._batch_raw = raw
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        self._batch_rows = []
        for line in lines:
            r = parse_detailed(line)
            if r:
                self._batch_rows.append({
                    "raw": line,
                    "lat": r["lat"],
                    "lon": r["lon"],
                    "format_label": r["format_label"],
                    "error": None,
                })
            else:
                self._batch_rows.append({
                    "raw": line,
                    "lat": 0,
                    "lon": 0,
                    "format_label": None,
                    "error": "无法识别",
                })
        self._refresh_batch_table()

    def _on_example_select(self, index: int) -> None:
        if index <= 0:
            return
        key = self._example_combo.itemData(index)
        if key and key in _BATCH_EXAMPLES:
            self._batch_textarea.setPlainText(_BATCH_EXAMPLES[key])
            self._on_batch_parse()
        self._example_combo.setCurrentIndex(0)

    def _on_batch_fmt(self, key: str) -> None:
        self._batch_fmt = key
        for k, b in self._batch_fmt_btns.items():
            b.setChecked(k == key)
        self._refresh_batch_table()

    def _on_batch_cs(self, key: str) -> None:
        self._batch_cs = key
        for k, b in self._batch_cs_btns.items():
            b.setChecked(k == key)
        self._refresh_batch_table()

    def _convert_coord(self, lat: float, lon: float) -> tuple[float, float]:
        if self._batch_cs == "gcj02":
            g = wgs84_to_gcj02(lon, lat)
            return g["lat"], g["lon"]
        if self._batch_cs == "bd09":
            b = wgs84_to_bd09(lon, lat)
            return b["lat"], b["lon"]
        return lat, lon

    def _format_val(self, val: float, is_lat: bool) -> str:
        """Format a single lat or lon value per current batch_fmt."""
        if self._batch_fmt == "dd":
            return f"{abs(val):.6f}"
        if self._batch_fmt == "dms":
            import math
            av = abs(val)
            d = int(av)
            mf = (av - d) * 60
            m = int(mf)
            s = round((mf - m) * 60, 1)
            return f"{d}°{m}'{s}\""
        if self._batch_fmt == "ddm":
            import math
            av = abs(val)
            d = int(av)
            m = round((av - d) * 60, 3)
            return f"{d}°{m}'"
        return f"{val:.6f}"

    def _refresh_batch_table(self) -> None:
        has_rows = bool(self._batch_rows)
        self._batch_controls.setVisible(has_rows)
        self._batch_table.setVisible(has_rows)
        self._batch_actions.setVisible(has_rows)

        if not has_rows:
            self._batch_table.setRowCount(0)
            return

        self._batch_table.setRowCount(len(self._batch_rows))
        for i, row in enumerate(self._batch_rows):
            self._batch_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            raw_item = QTableWidgetItem(row["raw"])
            if row["format_label"]:
                raw_item.setToolTip(row["format_label"])
            self._batch_table.setItem(i, 1, raw_item)
            if row["error"]:
                err_item = QTableWidgetItem(f"✗ {row['error']}")
                err_item.setForeground(
                    __import__("PyQt6.QtGui", fromlist=["QColor"]).QColor(_C["danger"])
                )
                self._batch_table.setItem(i, 2, err_item)
                self._batch_table.setItem(i, 3, QTableWidgetItem(""))
            else:
                clat, clon = self._convert_coord(row["lat"], row["lon"])
                self._batch_table.setItem(i, 2, QTableWidgetItem(self._format_val(clat, True)))
                self._batch_table.setItem(i, 3, QTableWidgetItem(self._format_val(clon, False)))

        self._batch_table.resizeColumnsToContents()
        self._batch_table.setColumnWidth(0, 32)

    def _batch_to_csv(self) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["#", "输入", "北纬", "东经"])
        for i, row in enumerate(self._batch_rows):
            if row["error"]:
                writer.writerow([i + 1, row["raw"], f"✗ {row['error']}", ""])
            else:
                clat, clon = self._convert_coord(row["lat"], row["lon"])
                writer.writerow([i + 1, row["raw"], self._format_val(clat, True), self._format_val(clon, False)])
        return buf.getvalue()

    def _on_copy_csv(self) -> None:
        csv_text = self._batch_to_csv()
        QApplication.clipboard().setText(csv_text)
        orig = self._csv_copy_btn.text()
        self._csv_copy_btn.setText("已复制 ✓")
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(1500, lambda: self._csv_copy_btn.setText(orig))

    def _on_download_csv(self) -> None:
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 CSV", "batch_coords.csv", "CSV Files (*.csv)"
        )
        if path:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                f.write(self._batch_to_csv())

    # ── Map (modal overlay) ───────────────────────────────────────────────────

    def _on_open_map(self) -> None:
        self._map_open = True
        self._map_selected_wgs = None
        self._show_map_modal()

    def _show_map_modal(self) -> None:
        """Create + show map modal overlay covering this view."""
        if self._map_overlay and self._map_overlay.isVisible():
            return

        # Build overlay
        overlay = QWidget(self)
        overlay.setObjectName("MapOverlay")
        overlay.setStyleSheet("QWidget#MapOverlay { background: rgba(0,0,0,0.62); }")
        overlay.resize(self.size())
        overlay.show()
        overlay.raise_()

        modal = QFrame(overlay)
        modal.setObjectName("MapModal")
        modal.setStyleSheet(
            f"QFrame#MapModal {{ background: {_C['panel']};"
            f" border: 1px solid {_C['border_med']}; border-radius: 10px; }}"
        )
        modal.setMinimumSize(700, 540)

        modal_lay = QVBoxLayout(modal)
        modal_lay.setContentsMargins(0, 0, 0, 0)
        modal_lay.setSpacing(0)

        # Header: search input + 搜索 + 关闭
        header = QFrame()
        header.setObjectName("MapHeader")
        header.setStyleSheet(
            f"QFrame#MapHeader {{ background: {_C['panel2']};"
            f" border-bottom: 1px solid {_C['border_med']}; border-radius: 10px 10px 0 0; }}"
        )
        hdr_lay = QHBoxLayout(header)
        hdr_lay.setContentsMargins(10, 8, 10, 8)
        hdr_lay.setSpacing(6)
        map_search = QLineEdit()
        map_search.setPlaceholderText("搜索地名...")
        map_search.setMinimumWidth(200)
        hdr_lay.addWidget(map_search, 1)
        srch_btn = QPushButton("搜索")
        srch_btn.setObjectName("Primary")
        srch_btn.setFixedWidth(60)
        hdr_lay.addWidget(srch_btn)
        close_btn = QPushButton("关闭")
        close_btn.setFixedWidth(60)
        hdr_lay.addWidget(close_btn)
        modal_lay.addWidget(header)

        # Map body
        map_body = QWidget()
        map_body.setMinimumHeight(400)
        map_body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        body_lay = QVBoxLayout(map_body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        modal_lay.addWidget(map_body, 1)

        # Footer: coord display + 确认选点
        footer = QFrame()
        footer.setObjectName("MapFooter")
        footer.setStyleSheet(
            f"QFrame#MapFooter {{ background: {_C['panel2']};"
            f" border-top: 1px solid {_C['border_med']}; border-radius: 0 0 10px 10px; }}"
        )
        ftr_lay = QHBoxLayout(footer)
        ftr_lay.setContentsMargins(12, 8, 12, 8)
        ftr_lay.setSpacing(8)

        coord_info = QWidget()
        coord_info.setStyleSheet("background: transparent;")
        ci_lay = QVBoxLayout(coord_info)
        ci_lay.setContentsMargins(0, 0, 0, 0)
        ci_lay.setSpacing(2)
        coord_display = QLabel("点击地图或拖拽标记选点")
        coord_display.setStyleSheet(
            f"font-family: monospace; color: {_C['accent']}; font-size: 12px; background: transparent;"
        )
        coord_formats = QLabel("")
        coord_formats.setStyleSheet(
            f"font-size: 11px; color: {_C['muted']}; background: transparent;"
        )
        ci_lay.addWidget(coord_display)
        ci_lay.addWidget(coord_formats)
        ftr_lay.addWidget(coord_info, 1)

        confirm_btn = QPushButton("确认选点")
        confirm_btn.setObjectName("Primary")
        confirm_btn.setFixedWidth(80)
        confirm_btn.setEnabled(False)
        ftr_lay.addWidget(confirm_btn)

        modal_lay.addWidget(footer)

        # Wire events
        def _close() -> None:
            self._map_open = False
            self._map_selected_wgs = None
            overlay.close()
            overlay.deleteLater()
            self._map_overlay = None
            self._web_view = None

        def _confirm() -> None:
            sel = self._map_selected_wgs
            if sel and is_valid(sel.get("lat", 0), sel.get("lon", 0)):
                dms_str = to_dms(sel["lat"], sel["lon"])
                self._input_edit.setText(dms_str)
            _close()

        close_btn.clicked.connect(_close)
        confirm_btn.clicked.connect(_confirm)

        def _map_search() -> None:
            q = map_search.text().strip()
            if q:
                self._call_map_js(f"doPlaceSearch({json.dumps(q)})")

        srch_btn.clicked.connect(_map_search)
        map_search.returnPressed.connect(_map_search)

        # Lazy WebEngineView
        self._ensure_web_view(map_body, body_lay, coord_display, coord_formats, confirm_btn)

        # Position modal centered in overlay
        overlay.resizeEvent = lambda e: self._reposition_modal(overlay, modal)
        self._reposition_modal(overlay, modal)

        self._map_overlay = overlay

        # Move marker if we already have parsed coords
        if self._parsed:
            gcj = wgs84_to_gcj02(self._parsed["lon"], self._parsed["lat"])
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(500, lambda: self._call_map_js(
                f"moveTo({gcj['lon']}, {gcj['lat']})"
            ))

    def _reposition_modal(self, overlay: QWidget, modal: QFrame) -> None:
        ow, oh = overlay.width(), overlay.height()
        mw = min(850, max(700, int(ow * 0.92)))
        mh = min(640, max(540, int(oh * 0.85)))
        modal.setGeometry(
            (ow - mw) // 2,
            (oh - mh) // 2,
            mw,
            mh,
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._map_overlay and self._map_overlay.isVisible():
            self._map_overlay.resize(self.size())

    def _ensure_web_view(
        self,
        map_body: QWidget,
        body_lay: QVBoxLayout,
        coord_display: QLabel,
        coord_formats: QLabel,
        confirm_btn: QPushButton,
    ) -> None:
        """Create QWebEngineView or fallback label; inject JS bridge."""
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            from PyQt6.QtCore import QObject, pyqtSlot as Slot
            from PyQt6.QtWebChannel import QWebChannel

            wv = QWebEngineView()
            wv.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

            class _Bridge(QObject):
                def __init__(self_b, view_ref: "CoordsView"):
                    super().__init__()
                    self_b._view = view_ref
                    self_b._display = coord_display
                    self_b._formats = coord_formats
                    self_b._confirm = confirm_btn

                @pyqtSlot(str)
                def onMarkerMoved(self_b, payload: str) -> None:  # noqa: N802
                    try:
                        data = json.loads(payload)
                        self_b._view._map_selected_wgs = data
                        self_b._confirm.setEnabled(True)
                        lat = data.get("lat", 0)
                        lon = data.get("lon", 0)
                        self_b._display.setText(
                            f"WGS-84: {lat:.6f}, {lon:.6f}"
                        )
                    except Exception:
                        pass

            self._bridge = _Bridge(self)
            channel = QWebChannel(wv.page())
            channel.registerObject("qt_bridge", self._bridge)
            wv.page().setWebChannel(channel)

            wv.setHtml(
                _inject_webchannel_shim(_MAP_HTML),
                QUrl("https://map.local/")
            )
            body_lay.addWidget(wv)
            self._web_view = wv

        except Exception as exc:  # noqa: BLE001
            fallback = QLabel(
                f"交互地图不可用（QWebEngineView 加载失败）\n{exc}"
            )
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fallback.setStyleSheet(
                f"color: {_C['muted']}; background: transparent; padding: 20px;"
            )
            body_lay.addWidget(fallback)
            self._web_view = fallback

    def _call_map_js(self, js: str) -> None:
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            if isinstance(self._web_view, QWebEngineView):
                self._web_view.page().runJavaScript(js)
        except Exception:
            pass
