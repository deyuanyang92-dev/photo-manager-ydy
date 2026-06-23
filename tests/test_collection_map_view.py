"""test_collection_map_view.py — 采集地图视图冒烟测试（offscreen）.

采集地图把 collection_records 的站位经纬度按 站位/断面/地区 三级聚合，画到原生
OSM 瓦片图上。本测试覆盖：构造、导航契约、无项目不崩、有数据加载点、粒度切换。

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_collection_map_view.py -v
"""
from __future__ import annotations

import os
import sqlite3

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from unittest.mock import MagicMock

from app.db.db_manager import ensure_schema
from app.services import collection_record_service as crs
from app.views.collection_map_view import CollectionMapView
from app.views.registry import ALL_VIEWS

_APP = QApplication.instance() or QApplication([])


def _seed_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    crs.upsert_record(conn, {"province": "ZJ", "site": "SMW", "station": "B2",
                             "collection_date": "20260518", "station_label": "北滩二区",
                             "lon": "121.0", "lat": "29.0"})
    crs.upsert_record(conn, {"province": "ZJ", "site": "SMW", "station": "H1",
                             "collection_date": "20260519",
                             "lon": "123.0", "lat": "31.0"})
    crs.upsert_record(conn, {"province": "FJ", "site": "XM", "station": "A1",
                             "collection_date": "20260601",
                             "lon": "118.0", "lat": "24.0"})
    return conn


def _ctx(db=None):
    ctx = MagicMock()
    ctx.get_db.return_value = db
    return ctx


def _view(db=None) -> CollectionMapView:
    v = CollectionMapView(_ctx(db))
    v.resize(900, 600)
    return v


class TestConstruction:
    def test_instantiates(self):
        v = _view()
        assert v is not None

    def test_nav_contract(self):
        assert CollectionMapView.view_id == "collection_map"
        assert CollectionMapView.nav_title == "采集地图"

    def test_registered_in_nav(self):
        assert CollectionMapView in ALL_VIEWS


class TestActivation:
    def test_no_project_does_not_crash(self):
        v = _view(db=None)
        v.on_activate()   # ctx.get_db() → None → empty state, no crash

    def test_loads_station_points(self):
        v = _view(db=_seed_db())
        v.on_activate()
        # 默认站位级 → 3 个站位点
        assert len(v._tile_map._points) == 3

    def test_switch_to_province_aggregates(self):
        v = _view(db=_seed_db())
        v.on_activate()
        v._set_level("province")
        QApplication.processEvents()
        # ZJ + FJ = 2 个地区点
        assert len(v._tile_map._points) == 2

    def test_switch_to_site_aggregates(self):
        v = _view(db=_seed_db())
        v.on_activate()
        v._set_level("site")
        QApplication.processEvents()
        assert len(v._tile_map._points) == 2   # SMW + XM


class TestInteraction:
    def test_point_click_sets_pending_filter_and_navigates(self):
        db = _seed_db()
        v = _view(db=db)
        v.on_activate()
        # 模拟点中第 0 个点
        v._on_point_clicked(0)
        # 句柄写到 ctx（跳转目标 CollectionRecordsView 消费）
        flt = getattr(v.ctx, "pending_record_filter", None)
        assert isinstance(flt, dict)
        assert "province" in flt


# ── 出版底图整合（Phase A）─────────────────────────────────────────────────────

def _make_basemap_image(tmp_path):
    from PIL import Image
    Image.new("RGB", (160, 100), (225, 238, 238)).save(tmp_path / "bm.png")
    return tmp_path / "bm.png"


class TestBasemapSwitch:
    def test_combo_present_osm_first(self):
        v = _view()
        assert v._basemap_combo.count() >= 1
        assert v._basemap_combo.itemData(0)["kind"] == "osm"

    def test_default_mode_osm(self):
        v = _view()
        assert v._stack.currentWidget() is v._tile_map

    def test_activate_image_shows_pub_widget(self, tmp_path):
        v = _view(db=_seed_db())
        img = _make_basemap_image(tmp_path)
        entry = {"id": "image:bm.png", "name": "bm", "kind": "image",
                 "source": str(img), "ext": ".png"}
        v._activate_basemap(entry)
        assert v._stack.currentWidget() is v._pub_map
        # 未校准 → 校准按钮启用
        assert v._calibrate_btn.isEnabled()

    def test_activate_osm_back(self, tmp_path):
        v = _view()
        img = _make_basemap_image(tmp_path)
        v._activate_basemap({"id": "image:bm.png", "name": "bm", "kind": "image",
                             "source": str(img), "ext": ".png"})
        v._activate_basemap({"id": "osm", "name": "OSM", "kind": "osm", "source": "", "ext": ""})
        assert v._stack.currentWidget() is v._tile_map
        assert not v._calibrate_btn.isEnabled()

    def test_activate_satellite_tile_stays_interactive(self):
        v = _view()
        entry = {
            "id": "satellite:test",
            "name": "卫星影像",
            "kind": "tile",
            "source": "",
            "ext": "",
            "tile_url": "https://tiles.example.test/{z}/{y}/{x}.jpg",
            "attribution": "Imagery © Test",
        }
        v._activate_basemap(entry)
        assert v._stack.currentWidget() is v._tile_map
        assert not v._calibrate_btn.isEnabled()
        assert v._is_publication_mode() is False
        assert v._tile_map._tile_source_id == "satellite:test"


class TestAddProject:
    """左侧「项目」卡片内的「+ 新建项目」入口。"""

    def test_add_button_present(self):
        v = _view()
        assert hasattr(v, "_add_proj_btn")
        assert v._add_proj_btn is not None

    def test_add_project_persists_selects_no_navigate(self, tmp_path, monkeypatch):
        new_dir = str(tmp_path / "proj_x")
        new_proj = {"name": "项目X", "directory": new_dir}

        class _FakeDialog:
            DialogCode = type("DC", (), {"Accepted": 1, "Rejected": 0})
            last_kwargs = None

            def __init__(self, **kw):
                _FakeDialog.last_kwargs = kw

            def exec(self):
                return _FakeDialog.DialogCode.Accepted

            def result_project(self):
                return new_proj

        saved: dict = {}
        monkeypatch.setattr("app.views.project_dialog.ProjectDialog", _FakeDialog)
        monkeypatch.setattr("app.views.overview_view._load_projects", lambda: [])
        monkeypatch.setattr(
            "app.services.project_service.save_project_descriptor",
            lambda _path, proj, **_kw: saved.setdefault("project", proj),
        )
        # _populate_projects 重建列表时读注册表 → 让它看到新项目
        monkeypatch.setattr(
            "app.views.collection_map_view.list_projects",
            lambda _path: [new_proj],
        )

        v = _view(db=None)
        v._on_add_project()

        # 1. 以 new 模式打开对话框
        assert _FakeDialog.last_kwargs is not None
        assert _FakeDialog.last_kwargs.get("mode") == "new"
        # 2. 持久化到注册表
        assert saved.get("project") == new_proj
        # 3. 选中新项目（停留在地图）
        assert v._project_filter == new_dir
        assert v.ctx.current_project_dir == new_dir


class TestImportCoords:
    """右栏工具条「导入经纬度」入口：复用 CoordImportDialog，导入后刷新地图。"""

    def test_import_button_present(self):
        v = _view()
        assert hasattr(v, "_import_btn")
        assert v._import_btn is not None

    def test_import_uses_selected_project_and_refreshes(self, tmp_path, monkeypatch):
        captured = {}

        class _FakeImportDialog:
            def __init__(self, db, parent=None):
                captured["db"] = db

            def exec(self):
                return True

        monkeypatch.setattr(
            "app.widgets.coord_import_dialog.CoordImportDialog", _FakeImportDialog
        )

        db = _seed_db()
        v = _view(db=db)
        target = str(tmp_path / "proj_sel")
        v._project_filter = target
        v._populate_projects = MagicMock()
        v._reload = MagicMock()

        v._on_import_coords()

        # 用选中项目的 db 打开对话框
        assert captured.get("db") is db
        # 导入成功 → 切到该项目并刷新
        assert v._project_filter == target
        v._populate_projects.assert_called_once()
        v._reload.assert_called_once()

    def test_import_all_projects_prompts_picker(self, tmp_path, monkeypatch):
        """未选具体项目（全部项目）→ 弹项目选择；取消则不开导入对话框。"""
        opened = {"import": False}

        class _FakeImportDialog:
            def __init__(self, db, parent=None):
                opened["import"] = True

            def exec(self):
                return True

        monkeypatch.setattr(
            "app.widgets.coord_import_dialog.CoordImportDialog", _FakeImportDialog
        )
        # 取消项目选择 → 返回 None，不进入导入
        monkeypatch.setattr(
            CollectionMapView, "_pick_target_project", lambda self: None
        )

        v = _view(db=_seed_db())
        v._project_filter = None
        v._reload = MagicMock()
        v._on_import_coords()

        assert opened["import"] is False
        v._reload.assert_not_called()


class TestExport:
    def test_export_osm_png(self, tmp_path):
        v = _view(db=_seed_db())
        v.on_activate()
        out = tmp_path / "osm.png"
        v._do_export(str(out))
        assert out.exists() and out.stat().st_size > 0

    def test_export_pub_pdf(self, tmp_path):
        v = _view(db=_seed_db())
        v.on_activate()
        img = _make_basemap_image(tmp_path)
        v._activate_basemap({"id": "image:bm.png", "name": "bm", "kind": "image",
                             "source": str(img), "ext": ".png"})
        out = tmp_path / "fig.pdf"
        v._do_export(str(out))
        assert out.exists() and out.stat().st_size > 0


class TestLeftPaneScroll:
    """采集地图左栏（项目卡 + 站位标识卡）整体可滚动 —— 窗口偏矮时底部控件
    （标签/字段/字号等）仍可触达。回归用户报告的「下面需要滑动但缺失」。"""

    def _shown(self, h: int) -> CollectionMapView:
        v = _view()
        v.resize(1000, h)
        v.show()
        QApplication.processEvents()
        return v

    def _left_scroll(self, v):
        from PyQt6.QtWidgets import QScrollArea
        for sa in v.findChildren(QScrollArea):
            if sa.objectName() == "LeftScroll":
                return sa
        return None

    def _style_scroll(self, v):
        from PyQt6.QtWidgets import QScrollArea
        for sa in v.findChildren(QScrollArea):
            if sa.objectName() == "StyleScroll":
                return sa
        return None

    def _body_splitter(self, v):
        from PyQt6.QtWidgets import QSplitter
        for sp in v.findChildren(QSplitter):
            if sp.objectName() == "MapBodySplitter":
                return sp
        return None

    def test_left_scroll_exists_and_configured(self):
        from PyQt6.QtCore import Qt
        v = self._shown(700)
        ls = self._left_scroll(v)
        assert ls is not None, "左栏缺少 LeftScroll 滚动容器"
        assert ls.widgetResizable() is True
        assert ls.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded

    def test_left_and_map_are_horizontally_resizable(self):
        v = self._shown(700)
        splitter = self._body_splitter(v)
        ls = self._left_scroll(v)
        assert splitter is not None, "采集地图主体缺少横向 QSplitter"
        assert splitter.count() == 2
        assert splitter.widget(0) is ls
        assert splitter.handleWidth() >= 10
        assert ls.minimumWidth() < ls.maximumWidth()

    def test_style_scroll_exists_and_configured(self):
        from PyQt6.QtCore import Qt
        v = self._shown(700)
        ss = self._style_scroll(v)
        assert ss is not None, "站位标识卡缺少 StyleScroll 滚动容器"
        assert ss.widgetResizable() is True
        assert ss.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        assert ss.maximumHeight() == 360

    def test_style_panel_lives_inside_left_scroll(self):
        v = self._shown(700)
        ls = self._left_scroll(v)
        # 样式面板必须是左栏滚动容器的后代，才能随左栏一起滚动
        assert v._style_panel in ls.findChildren(type(v._style_panel))

    def test_style_panel_lives_inside_style_scroll(self):
        v = self._shown(700)
        ss = self._style_scroll(v)
        assert ss.widget() is v._style_panel

    def test_bottom_reachable_when_window_short(self):
        # 矮窗口下内容溢出 → 左栏滚动条可用（maximum>0），底部控件可达
        v = self._shown(560)
        ls = self._left_scroll(v)
        assert ls.widget().sizeHint().height() > ls.viewport().height()
        assert ls.verticalScrollBar().maximum() > 0

    def test_style_scroll_can_scroll_marker_form(self):
        v = self._shown(700)
        ss = self._style_scroll(v)
        assert ss.widget().sizeHint().height() > ss.viewport().height()
        assert ss.verticalScrollBar().maximum() > 0


# ── 编辑模式：点空白新增 · 拖点移动 · 手输经纬度 ──────────────────────────────

class TestEditMode:
    def _ctx_db(self, db):
        ctx = MagicMock()
        ctx.get_db.return_value = db
        ctx.current_project_root = None
        ctx.current_project_dir = None
        return ctx

    def _view_with_project(self, db):
        v = CollectionMapView(self._ctx_db(db))
        v.resize(900, 600)
        v._project_filter = "/tmp/proj-x"   # 选中单个项目
        v._update_edit_buttons()
        return v

    def test_edit_buttons_absent_without_project(self):
        v = _view(db=_seed_db())
        v._project_filter = None
        v._update_edit_buttons()
        assert v._add_point_btn.isEnabled() is False
        assert v._input_coord_btn.isEnabled() is False

    def test_edit_buttons_enabled_with_single_project(self):
        v = self._view_with_project(_seed_db())
        assert v._add_point_btn.isEnabled() is True
        assert v._input_coord_btn.isEnabled() is True
        assert "写入：proj-x" in v._edit_status_lbl.text()
        assert "可拖点" in v._edit_status_lbl.text()

    def test_edit_buttons_enabled_all_projects_with_current_project(self):
        db = _seed_db()
        ctx = self._ctx_db(db)
        ctx.current_project_dir = "/tmp/current-proj"
        v = CollectionMapView(ctx)
        v.resize(900, 600)
        v._project_filter = None
        v._update_edit_buttons()
        assert v._edit_target_project() == "/tmp/current-proj"
        assert v._add_point_btn.isEnabled() is True
        assert v._input_coord_btn.isEnabled() is True
        assert "写入：current-proj" in v._edit_status_lbl.text()
        assert "全部视图" in v._edit_status_lbl.text()
        assert v._tile_map.point_drag_enabled is False

    def test_drag_disabled_when_not_station_level(self):
        v = self._view_with_project(_seed_db())
        assert v._tile_map.point_drag_enabled is True
        v._set_level("site")
        assert v._tile_map.point_drag_enabled is False
        assert "拖点需站位层级" in v._edit_status_lbl.text()

    def test_toggle_sets_interactive_points_and_hint(self):
        v = self._view_with_project(_seed_db())
        v._set_edit_mode(True)
        assert v._edit_mode is True
        assert v._tile_map.interactive_points is True
        assert v._edit_hint.isHidden() is False
        assert "写入「proj-x」" in v._edit_hint.text()
        v._set_edit_mode(False)
        assert v._tile_map.interactive_points is False
        assert v._edit_hint.isHidden() is True

    def test_point_create_new_record_writes_db(self, monkeypatch):
        db = _seed_db()
        v = self._view_with_project(db)
        # mock 对话框：返回新建动作
        from unittest.mock import patch
        fake = MagicMock()
        fake.exec.return_value = 1   # QDialog.DialogCode.Accepted
        fake.result_point.return_value = {
            "action": "new", "province": "ZJ", "site": "SMW", "station": "B9",
            "collection_date": "20260622", "zone": "intertidal",
            "lon": 122.5, "lat": 30.5,
        }
        with patch("app.widgets.collection_point_dialog.CollectionPointDialog",
                   return_value=fake):
            v._on_point_create(122.5, 30.5)
        rec = crs.lookup_record(db, "ZJ", "SMW", "B9", "20260622")
        assert rec is not None
        assert rec["lon"] == pytest.approx(122.5)
        assert rec["lat"] == pytest.approx(30.5)

    def test_point_create_all_projects_writes_current_project(self, monkeypatch):
        db = _seed_db()
        ctx = self._ctx_db(db)
        ctx.current_project_dir = "/tmp/current-proj"
        v = CollectionMapView(ctx)
        v.resize(900, 600)
        v._project_filter = None
        ctx.get_db.reset_mock()
        from unittest.mock import patch
        fake = MagicMock()
        fake.exec.return_value = 1
        fake.result_point.return_value = {
            "action": "new", "province": "ZJ", "site": "SMW", "station": "C1",
            "collection_date": "20260622", "zone": "intertidal",
            "lon": 122.6, "lat": 30.6,
        }
        with patch("app.widgets.collection_point_dialog.CollectionPointDialog",
                   return_value=fake):
            v._on_point_create(122.6, 30.6)
        ctx.get_db.assert_any_call("/tmp/current-proj")
        rec = crs.lookup_record(db, "ZJ", "SMW", "C1", "20260622")
        assert rec is not None
        assert rec["lon"] == pytest.approx(122.6)

    def test_point_create_bind_updates_station_coords(self, monkeypatch):
        db = _seed_db()   # B2 已有 (121,29)
        v = self._view_with_project(db)
        from unittest.mock import patch
        fake = MagicMock()
        fake.exec.return_value = 1
        fake.result_point.return_value = {
            "action": "bind", "province": "ZJ", "site": "SMW", "station": "B2",
            "lon": 199.0, "lat": 39.0,
        }
        with patch("app.widgets.collection_point_dialog.CollectionPointDialog",
                   return_value=fake):
            v._on_point_create(199.0, 39.0)
        rec = crs.lookup_record(db, "ZJ", "SMW", "B2", "20260518")
        assert rec["lon"] == pytest.approx(199.0)

    def test_point_moved_updates_station_after_confirm(self, monkeypatch):
        db = _seed_db()
        v = self._view_with_project(db)
        v.on_activate()
        # 找到 B2 点的索引
        idx = next(i for i, p in enumerate(v._points) if p.get("station") == "B2")
        from PyQt6.QtWidgets import QMessageBox
        monkeypatch.setattr("app.views.collection_map_view.ui.question",
                            lambda *a, **k: QMessageBox.StandardButton.Yes)
        v._on_point_moved(idx, 150.0, 35.0)
        rec = crs.lookup_record(db, "ZJ", "SMW", "B2", "20260518")
        assert rec["lon"] == pytest.approx(150.0)
        assert rec["lat"] == pytest.approx(35.0)

    def test_point_moved_cancelled_leaves_coords(self, monkeypatch):
        db = _seed_db()
        v = self._view_with_project(db)
        v.on_activate()
        idx = next(i for i, p in enumerate(v._points) if p.get("station") == "B2")
        from PyQt6.QtWidgets import QMessageBox
        monkeypatch.setattr("app.views.collection_map_view.ui.question",
                            lambda *a, **k: QMessageBox.StandardButton.No)
        v._on_point_moved(idx, 150.0, 35.0)
        rec = crs.lookup_record(db, "ZJ", "SMW", "B2", "20260518")
        assert rec["lon"] == pytest.approx(121.0)   # 未变
