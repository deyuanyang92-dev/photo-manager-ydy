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


class TestFreeCompose:
    def test_free_compose_names_output_in_incoming(self, tmp_path):
        """Free compose output basename must start with '自由合成-' if no name given."""
        incoming_dir = str(tmp_path / "incoming-jpg")
        os.makedirs(incoming_dir)
        from app.views.workbench_view import _free_compose_output_name
        name1 = _free_compose_output_name(incoming_dir, None)
        assert name1.startswith("自由合成-")
        assert name1.endswith(".tif")
        # Create first file to test increment
        Path(os.path.join(incoming_dir, name1)).write_bytes(b"X")
        name2 = _free_compose_output_name(incoming_dir, None)
        assert name2 != name1

    def test_free_compose_user_name(self, tmp_path):
        """User-provided name must be used (sanitized)."""
        incoming_dir = str(tmp_path / "incoming-jpg")
        os.makedirs(incoming_dir)
        from app.views.workbench_view import _free_compose_output_name
        name = _free_compose_output_name(incoming_dir, "my output")
        assert name == "my output.tif"

    def test_free_compose_user_name_conflict_falls_back(self, tmp_path):
        """If user name already exists, fall back to auto-naming."""
        incoming_dir = str(tmp_path / "incoming-jpg")
        os.makedirs(incoming_dir)
        Path(os.path.join(incoming_dir, "my output.tif")).write_bytes(b"X")
        from app.views.workbench_view import _free_compose_output_name
        # When user name conflicts, try the same name again → auto
        name = _free_compose_output_name(incoming_dir, "my output")
        # Falls back to 自由合成-1.tif since user_name conflicts
        assert name.startswith("自由合成-")


# ── Collision guard on organise  #cursor ─────────────────────────────────────

class TestOrganiseCollisionGuard:
    """archive_service does not overwrite silently — workbench checks for ZIP collision."""

    def test_archive_group_creates_restorable_zip(self, tmp_path):
        """archive_group creates an archive that software restores as JPGs."""
        import hashlib
        import zipfile
        from app.services.archive_service import archive_group, restore_archive
        jpg1 = tmp_path / "IMG_001.jpg"
        jpg2 = tmp_path / "IMG_002.jpg"
        jpg1.write_bytes(b"\xff\xd8\xff" * 100)
        jpg2.write_bytes(b"\xff\xd8\xff" * 100)
        hashes = {
            jpg1.name: hashlib.sha256(jpg1.read_bytes()).hexdigest(),
            jpg2.name: hashlib.sha256(jpg2.read_bytes()).hexdigest(),
        }
        tiff = tmp_path / "result.tif"
        tiff.write_bytes(b"IIX" * 1000)
        result = archive_group(
            jpg_paths=[str(jpg1), str(jpg2)],
            tiff_path=str(tiff),
            project_dir=str(tmp_path),
            delete_jpg=False,
        )
        assert result.ok
        assert os.path.isfile(result.zip_path)
        zip_name = Path(result.zip_path).name
        assert zip_name == "result.zip"
        with zipfile.ZipFile(result.zip_path) as zf:
            names = sorted(zf.namelist())
        if result.manifest["format"] == "jxl-zip":
            assert names == ["IMG_001.jxl", "IMG_002.jxl", "manifest.json"]
        else:
            assert names == ["IMG_001.jpg", "IMG_002.jpg"]
            assert "manifest.json" not in names

        restored_dir = tmp_path / "restored"
        restored = restore_archive(result.zip_path, str(restored_dir))
        assert restored.ok
        assert {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in restored_dir.glob("*.jpg")
        } == hashes

    def test_archive_group_deletes_loose_jpg_after_archive_verified(self, tmp_path):
        """Default product behavior: verified archive replaces loose JPG files."""
        import hashlib
        from app.services.archive_service import archive_group, restore_archive

        jpg1 = tmp_path / "IMG_001.jpg"
        jpg2 = tmp_path / "IMG_002.jpg"
        jpg1.write_bytes(b"\xff\xd8\xff\xe0jpg1")
        jpg2.write_bytes(b"\xff\xd8\xff\xe0jpg2")
        hashes = {
            jpg1.name: hashlib.sha256(jpg1.read_bytes()).hexdigest(),
            jpg2.name: hashlib.sha256(jpg2.read_bytes()).hexdigest(),
        }
        tiff = tmp_path / "result.tif"
        tiff.write_bytes(b"tif")

        result = archive_group(
            jpg_paths=[str(jpg1), str(jpg2)],
            tiff_path=str(tiff),
            project_dir=str(tmp_path),
            delete_jpg=True,
        )

        assert result.ok
        assert result.delete_jpg is True
        assert not jpg1.exists()
        assert not jpg2.exists()
        assert tiff.exists()

        restored_dir = tmp_path / "restored"
        restored = restore_archive(result.zip_path, str(restored_dir))
        assert restored.ok
        assert {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in restored_dir.glob("*.jpg")
        } == hashes

    def test_archive_group_adaptively_deflates_only_when_smaller(self, tmp_path):
        """Plain JPG ZIP keeps restoration exact and avoids expanding hard-to-compress files."""
        import hashlib
        import zipfile

        from app.services.archive_service import archive_group

        compressible = tmp_path / "compressible.jpg"
        incompressible = tmp_path / "incompressible.jpg"
        compressible.write_bytes(b"\xff\xd8" + (b"A" * 8192) + b"\xff\xd9")
        incompressible.write_bytes(os.urandom(8192))
        tiff = tmp_path / "result.tif"
        tiff.write_bytes(b"tif")

        from unittest.mock import patch

        with patch("app.services.archive_service.has_cjxl", return_value=False):
            with patch("app.services.archive_service.has_djxl", return_value=False):
                result = archive_group(
                    jpg_paths=[str(compressible), str(incompressible)],
                    tiff_path=str(tiff),
                    project_dir=str(tmp_path),
                    delete_jpg=False,
                    method="adaptive",
                )

        with zipfile.ZipFile(result.zip_path) as zf:
            infos = {info.filename: info for info in zf.infolist()}
            assert infos["compressible.jpg"].compress_type == zipfile.ZIP_DEFLATED
            assert infos["incompressible.jpg"].compress_type == zipfile.ZIP_STORED
            for src in (compressible, incompressible):
                restored = zf.read(src.name)
                assert hashlib.sha256(restored).hexdigest() == hashlib.sha256(
                    src.read_bytes()
                ).hexdigest()
        methods = {
            f["archiveName"]: f["zipCompression"]
            for f in result.manifest["files"]
        }
        assert methods == {
            "compressible.jpg": "deflate9",
            "incompressible.jpg": "store",
        }

    def test_organize_preview_second_seq_avoids_collision(self, tmp_path):
        """organize_preview must increment seq when seq-1 TIFF already present."""
        from app.services.organize_service import organize_preview
        db = _make_db(str(tmp_path / "project.db"))
        results_dir = str(tmp_path / "results")
        os.makedirs(results_dir)
        uid = "FJ-XM-B2-TST001-T95E-20260601"
        # Create existing seq-1 TIFF
        (Path(results_dir) / "FJ-XM-B2-TST001-1-T95E-20260601.tif").write_bytes(b"T")
        prev = organize_preview(db, uid, results_dir=results_dir)
        assert prev.next_seq == 2
        db.close()


# ── GroupingPanel delete / clear from wiring perspective  #cursor ────────────

class TestGroupingDeleteClearWiring:
    """Service-layer persistence after delete / clear group."""

    def test_clear_group_persists_via_save_grouping(self, tmp_path):
        from app.services.grouping_service import (
            Group, SpecimenGrouping, save_grouping, load_grouping,
        )
        db = _make_db(str(tmp_path / "project.db"))
        uid = "FJ-XM-B2-TST001-T95E-20260601"
        groups = [
            Group(group_index=0, jpg_paths=["/a.jpg", "/b.jpg"]),
        ]
        save_grouping(db, uid, groups, clean_phantoms=False)
        # Simulate clear_group
        loaded = load_grouping(db, uid)
        loaded.groups[0].jpg_paths = []
        save_grouping(db, uid, loaded.groups, clean_phantoms=False)
        reloaded = load_grouping(db, uid)
        assert reloaded.groups[0].jpg_paths == []
        db.close()

    def test_delete_group_persists_via_save_grouping(self, tmp_path):
        from app.services.grouping_service import (
            Group, save_grouping, load_grouping,
        )
        db = _make_db(str(tmp_path / "project.db"))
        uid = "FJ-XM-B2-TST001-T95E-20260601"
        groups = [
            Group(group_index=0, jpg_paths=["/a.jpg"]),
            Group(group_index=1, jpg_paths=["/c.jpg"]),
        ]
        save_grouping(db, uid, groups, clean_phantoms=False)
        # Simulate delete group 0
        loaded = load_grouping(db, uid)
        loaded.groups = [g for g in loaded.groups if g.group_index != 0]
        save_grouping(db, uid, loaded.groups, clean_phantoms=False)
        reloaded = load_grouping(db, uid)
        assert len(reloaded.groups) == 1
        assert reloaded.groups[0].group_index == 1
        db.close()


class TestMultiUidResultsScope:
    """侧栏 Ctrl/Shift 多选编号 → 成果区只显示这些编号的成果(用户 2026-07-10)."""

    def test_multi_select_loads_only_selected_uids(self, monkeypatch):
        from app.views import workbench_view as wv

        captured: list = []

        class _Stub:
            def __init__(self):
                self.title = None

            def load_many(self, groups, *, title=None):
                captured.append(([g["uid"] for g in groups], title))

            def clear(self):
                captured.append(("clear", None))

        view = wv.WorkbenchView.__new__(wv.WorkbenchView)
        view._results = _Stub()
        view._status_message = lambda *_a, **_k: None
        monkeypatch.setattr(
            view, "_groups_for_uids",
            lambda uids: [{"uid": u, "tiffs": [{"path": f"/{u}.tif"}], "zips": []} for u in uids],
            raising=False,
        )
        view._on_specimen_selection_scope_changed(["U-1", "U-3"])

        assert captured, "多选应触发成果区加载"
        uids, title = captured[-1]
        assert uids == ["U-1", "U-3"]
        assert title and "2" in title, f"标题应标明所选编号数, 实际: {title!r}"

    def test_plain_single_click_does_not_reload_results(self, monkeypatch):
        """单击已由 specimen_selected 加载成果; scope 信号不得重复查库(卡顿源)。"""
        from app.views import workbench_view as wv

        view = wv.WorkbenchView.__new__(wv.WorkbenchView)
        view._multi_scope_active = False
        calls: list = []
        monkeypatch.setattr(
            view, "_on_show_current_results", lambda: calls.append("current"), raising=False
        )
        view._on_specimen_selection_scope_changed(["U-1"])
        assert calls == [], "未进过多选时, 单选不应触发额外的成果重载"

    def test_leaving_multi_select_restores_current_uid_view(self, monkeypatch):
        from app.views import workbench_view as wv

        view = wv.WorkbenchView.__new__(wv.WorkbenchView)
        view._results = type("S", (), {"load_many": lambda *a, **k: None})()
        view._status_message = lambda *_a, **_k: None
        monkeypatch.setattr(view, "_groups_for_uids", lambda uids: [], raising=False)
        calls: list = []
        monkeypatch.setattr(
            view, "_on_show_current_results", lambda: calls.append("current"), raising=False
        )

        view._on_specimen_selection_scope_changed(["U-1", "U-2"])   # 进入多选
        view._on_specimen_selection_scope_changed(["U-1"])          # 退回单选
        assert calls == ["current"], "退出多选必须恢复「当前编号」视图"


class TestUnboundResultsVisible:
    """解绑后 TIF 仍在 results/ 里 —— 「全部」模式必须把它列出来(未关联成果),
    否则解绑 = 让文件从界面上消失, 用户无法改绑(用户 2026-07-10)。"""

    def test_show_all_appends_unbound_group(self, monkeypatch, tmp_path):
        from app.views import workbench_view as wv

        loaded: list = []

        import types

        view = wv.WorkbenchView.__new__(wv.WorkbenchView)
        view.ctx = types.SimpleNamespace(
            get_db=lambda: object(), current_project_dir=str(tmp_path)
        )
        view._results = type(
            "S", (), {"load_many": lambda _s, groups, **k: loaded.append(groups)}
        )()
        view._status_message = lambda *_a, **_k: None
        monkeypatch.setattr(
            view, "_groups_for_uids",
            lambda uids: [{"uid": "U-1", "tiffs": [{"path": "/a.tif"}], "zips": []}],
            raising=False,
        )
        monkeypatch.setattr(view, "_project_uids", lambda: ["U-1"], raising=False)
        monkeypatch.setattr(
            view, "_unbound_result_group",
            lambda: {"uid": "未关联成果", "tiffs": [{"path": "/loose.tif"}], "zips": []},
            raising=False,
        )

        view._on_show_all_results()

        assert loaded, "全部模式应加载分组"
        uids = [g["uid"] for g in loaded[-1]]
        assert uids[-1] == "未关联成果", f"未关联成果应排在最后一组, 实际 {uids}"
