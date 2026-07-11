"""tests/test_restore_archive_target.py — 「还原原片」默认落回工作区待处理区.

用户裁定(2026-07-10):合成错了要撤回时,还原的 JPG 必须自动回到工作区的
``incoming-jpg/``(待处理区),不得每次弹目录选择框让用户挑保存位置。
参见 PROJECT_MEMORY「incoming-jpg 是待处理区」。
"""
from __future__ import annotations

import os
import types
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from app.views.workbench_supplementary_workflow import (
    WorkbenchSupplementaryWorkflowMixin,
)


class _Harness(WorkbenchSupplementaryWorkflowMixin):
    """只装配还原路径所需的最小上下文(不构造整个 QWidget 工作台)。"""

    def __init__(self, project_dir: str = "") -> None:
        self.ctx = types.SimpleNamespace(
            current_project_dir=project_dir, settings=None
        )
        self.started: list = []

    def _resolve_capture_subdirs(self):
        return "incoming-jpg", "results"

    def _status_message(self, *_a, **_k) -> None:
        pass


def _make_zip(tmp_path) -> str:
    z = tmp_path / "results" / "UID-1-20260618.zip"
    z.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("a.jpg", b"\xff\xd8\xff")
        zf.writestr("b.jpg", b"\xff\xd8\xff")
    return str(z)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "incoming-jpg").mkdir()
    return tmp_path


def test_restore_defaults_to_incoming_without_asking(project, monkeypatch):
    zip_path = _make_zip(project)
    h = _Harness(str(project))

    asked: list = []
    monkeypatch.setattr(
        "app.utils.ui.get_existing_directory",
        lambda *a, **k: asked.append(a) or "",
    )

    started: list = []

    class _FakeWorker:
        def __init__(self, zp, out, *, overwrite, file_count, parent=None):
            started.append({"zip": zp, "out": out, "overwrite": overwrite})
            self.started = types.SimpleNamespace(connect=lambda *_: None)
            self.finished = types.SimpleNamespace(connect=lambda *_: None)
            self.failed = types.SimpleNamespace(connect=lambda *_: None)
            self.progress = types.SimpleNamespace(connect=lambda *_: None)

        def start(self):
            pass

    monkeypatch.setattr("app.workers.restore_worker.RestoreWorker", _FakeWorker)

    h._on_restore_archive(zip_path)

    assert not asked, "还原不得弹目录选择框(默认回工作区待处理区)"
    assert started, "还原 worker 应被启动"
    assert started[0]["out"] == str(project / "incoming-jpg"), (
        f"应还原到 incoming-jpg/, 实际 {started[0]['out']}"
    )
    assert started[0]["overwrite"] is False, "同名 JPG 默认跳过, 不覆盖"


def test_restore_falls_back_to_picker_when_no_project(tmp_path, monkeypatch):
    """无当前项目(无处可还原)时才退回目录选择框。"""
    zip_path = _make_zip(tmp_path)
    h = _Harness("")
    h.ctx.current_project_dir = None

    picked = str(tmp_path / "elsewhere")
    os.makedirs(picked, exist_ok=True)
    monkeypatch.setattr("app.utils.ui.get_existing_directory", lambda *a, **k: picked)
    monkeypatch.setattr("app.utils.ui.question", lambda *a, **k: None)

    started: list = []

    class _FakeWorker:
        def __init__(self, zp, out, *, overwrite, file_count, parent=None):
            started.append(out)
            self.started = types.SimpleNamespace(connect=lambda *_: None)
            self.finished = types.SimpleNamespace(connect=lambda *_: None)
            self.failed = types.SimpleNamespace(connect=lambda *_: None)
            self.progress = types.SimpleNamespace(connect=lambda *_: None)

        def start(self):
            pass

    monkeypatch.setattr("app.workers.restore_worker.RestoreWorker", _FakeWorker)
    h._on_restore_archive(zip_path)
    assert started and started[0] == picked


# ── 还原原片 = 撤销整理(用户 2026-07-10 裁定) ───────────────────────────────
#
# 使用场景:用户合成/整理错了, 点 ZIP 卡上的「还原原片」是想**撤回这次整理**。
# 原片回到待处理区之后, ZIP 若还挂在成果区 = 状态矛盾(同一批 JPG 既在待处理
# 又在归档里, 看起来像什么都没发生)。所以:
#   · ZIP 属于某编号的组 → 走「撤销整理」: JPG 还原 + 项目内 ZIP 直接删除 +
#     组退回 composed → 成果区那行 ZIP 消失, TIF 保留
#   · 孤儿 ZIP(不属于任何组) → 保留旧行为: 只抽副本到待处理区


class _UndoHarness(_Harness):
    """再挂上真实的撤销整理链路所需成员。"""

    def __init__(self, project_dir: str, db) -> None:
        super().__init__(project_dir)
        self.ctx.get_db = lambda: db
        self._grouping = types.SimpleNamespace(load_grouping=lambda *a, **k: None)

    # 复用真实实现(来自 result workflow mixin)
    from app.views.workbench_result_workflow import (
        WorkbenchResultWorkflowMixin as _RW,
    )
    _on_undo_organise = _RW._on_undo_organise
    _retire_zip = _RW._retire_zip

    def _refresh_results_column(self, uid, grouping=None) -> None:
        pass


def test_restore_of_organized_group_undoes_organise(project, monkeypatch):
    """组内 ZIP 的「还原原片」= 撤销整理: JPG 回原位 + ZIP 删除 + 组回 composed。"""
    import sqlite3
    from app.db import db_manager
    from app.services.grouping_service import Group, load_grouping, save_grouping

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db_manager.ensure_schema(db)

    uid = "FJ-XM-A01-DLC001-T95E-20260601"
    jpg_orig = project / "incoming-jpg" / "P001.jpg"
    tiff = project / "results" / f"{uid.replace('-T95E', '-1-T95E')}.tif"
    tiff.parent.mkdir(parents=True, exist_ok=True)
    tiff.write_bytes(b"tif")

    zip_path = project / "results" / "r.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("P001.jpg", b"\xff\xd8\xffJPGDATA")

    save_grouping(
        db, uid,
        [Group(group_index=0, composed_tiff_path=str(tiff),
               archive_zip=str(zip_path), jpg_paths=[str(jpg_orig)],
               status="organized")],
        clean_phantoms=False,
    )

    h = _UndoHarness(str(project), db)
    from PyQt6.QtWidgets import QMessageBox
    questions: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(
            lambda *a, **k: (
                questions.append(str(a[2])), QMessageBox.StandardButton.Yes
            )[1]
        ),
    )
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

    h._on_restore_archive(str(zip_path))

    groups = load_grouping(db, uid).groups
    assert len(groups) == 1, "组必须保留(TIF 还在)"
    g = groups[0]
    assert not g.archive_zip, "ZIP 登记必须清除 → 成果区那行 ZIP 消失"
    assert g.status == "composed", "组退回「已合成、待整理」"
    assert jpg_orig.is_file(), "JPG 应还原回原位置(待处理区)"
    assert not zip_path.exists(), "恢复成功后项目内 ZIP 应直接删除"
    assert not (project / "_retired-zip").exists(), "不应再创建 ZIP 备份目录"
    assert tiff.is_file(), "TIF 母版不动(红线)"
    assert questions and "直接删除" in questions[0]
    assert "_retired-zip" not in questions[0]


def test_restore_of_orphan_zip_keeps_copy_semantics(project, monkeypatch):
    """孤儿 ZIP(不属于任何组): 保持抽副本到待处理区的旧行为, ZIP 原地不动。"""
    import sqlite3
    from app.db import db_manager

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db_manager.ensure_schema(db)

    zip_path = _make_zip(project)
    h = _UndoHarness(str(project), db)

    started: list = []

    class _FakeWorker:
        def __init__(self, zp, out, *, overwrite, file_count, parent=None):
            started.append(out)
            self.started = types.SimpleNamespace(connect=lambda *_: None)
            self.finished = types.SimpleNamespace(connect=lambda *_: None)
            self.failed = types.SimpleNamespace(connect=lambda *_: None)

        def start(self):
            pass

    monkeypatch.setattr("app.workers.restore_worker.RestoreWorker", _FakeWorker)
    h._on_restore_archive(zip_path)

    assert started and started[0] == str(project / "incoming-jpg")
    assert os.path.isfile(zip_path), "孤儿 ZIP 不动"


def test_restore_group_without_jpg_paths_still_removes_zip(project, monkeypatch):
    """组存在但没记录原 JPG 路径(如改绑挂上的成果): 抽副本到待处理区成功后,
    同样必须清 ZIP 登记 + 删除项目内 ZIP + 组回 composed —— 语义一致, 不留矛盾状态。"""
    import sqlite3
    from app.db import db_manager
    from app.services.grouping_service import Group, load_grouping, save_grouping

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db_manager.ensure_schema(db)

    uid = "FJ-XM-A01-DLC001-T95E-20260601"
    zip_path = project / "results" / "nopaths.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("P001.jpg", b"\xff\xd8\xffX")
    save_grouping(
        db, uid,
        [Group(group_index=0, composed_tiff_path="", archive_zip=str(zip_path),
               jpg_paths=[], status="organized")],
        clean_phantoms=False,
    )

    h = _UndoHarness(str(project), db)

    class _FakeWorker:
        def __init__(self, zp, out, *, overwrite, file_count, parent=None):
            self.started = types.SimpleNamespace(connect=lambda *_: None)
            self.finished = types.SimpleNamespace(connect=lambda *_: None)
            self.failed = types.SimpleNamespace(connect=lambda *_: None)

        def start(self):
            pass

    monkeypatch.setattr("app.workers.restore_worker.RestoreWorker", _FakeWorker)
    from app.utils import ui as _ui
    monkeypatch.setattr(_ui, "info", lambda *a, **k: None)

    h._on_restore_archive(str(zip_path))
    # worker 完成(成功) → finalize
    result = types.SimpleNamespace(
        ok=True, count=1, skipped=[], failures=[],
        output_dir=str(project / "incoming-jpg"),
    )
    h._on_restore_finished(result)

    g = load_grouping(db, uid).groups[0]
    assert not g.archive_zip and g.status == "composed"
    assert not zip_path.exists()
    assert not (project / "_retired-zip").exists()


def test_restore_failure_leaves_zip_registered(project, monkeypatch):
    """还原失败 → 绝不动 ZIP 登记(否则原片没回来、归档记录还丢了)。"""
    import sqlite3
    from app.db import db_manager
    from app.services.grouping_service import Group, load_grouping, save_grouping

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db_manager.ensure_schema(db)

    uid = "FJ-XM-A01-DLC001-T95E-20260601"
    zip_path = project / "results" / "fail.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("P001.jpg", b"\xff\xd8\xffX")
    save_grouping(
        db, uid,
        [Group(group_index=0, composed_tiff_path="", archive_zip=str(zip_path),
               jpg_paths=[], status="organized")],
        clean_phantoms=False,
    )
    h = _UndoHarness(str(project), db)

    class _FakeWorker:
        def __init__(self, *a, **k):
            self.started = types.SimpleNamespace(connect=lambda *_: None)
            self.finished = types.SimpleNamespace(connect=lambda *_: None)
            self.failed = types.SimpleNamespace(connect=lambda *_: None)

        def start(self):
            pass

    monkeypatch.setattr("app.workers.restore_worker.RestoreWorker", _FakeWorker)
    from app.utils import ui as _ui
    monkeypatch.setattr(_ui, "critical", lambda *a, **k: None)

    h._on_restore_archive(str(zip_path))
    h._on_restore_finished(types.SimpleNamespace(ok=False, reason="bad", failures=[]))

    g = load_grouping(db, uid).groups[0]
    assert g.archive_zip and g.status == "organized", "失败不得撤登记"
    assert zip_path.exists()


def test_undo_organise_keeps_zip_when_state_save_fails(project, monkeypatch):
    """JPG 虽已恢复，但数据库没写成时必须保留 ZIP，避免失去可重试归档。"""
    import sqlite3
    from PyQt6.QtWidgets import QMessageBox
    from app.db import db_manager
    from app.services import grouping_service
    from app.services.grouping_service import Group, load_grouping, save_grouping

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db_manager.ensure_schema(db)

    uid = "FJ-XM-A01-DLC001-T95E-20260601"
    jpg_orig = project / "incoming-jpg" / "P001.jpg"
    zip_path = project / "results" / "save-fail.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("P001.jpg", b"\xff\xd8\xffSAFE")
    save_grouping(
        db,
        uid,
        [
            Group(
                group_index=0,
                composed_tiff_path="",
                archive_zip=str(zip_path),
                jpg_paths=[str(jpg_orig)],
                status="organized",
            )
        ],
        clean_phantoms=False,
    )

    h = _UndoHarness(str(project), db)
    grouping = load_grouping(db, uid)
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *a, **k: warnings.append(str(a[2]))),
    )
    monkeypatch.setattr(
        grouping_service,
        "save_grouping",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db busy")),
    )

    h._on_undo_organise(uid, grouping, grouping.groups[0])

    persisted = load_grouping(db, uid).groups[0]
    assert jpg_orig.is_file(), "恢复结果不应回滚删除"
    assert zip_path.is_file(), "状态未写成时 ZIP 必须保留"
    assert persisted.archive_zip and persisted.status == "organized"
    assert grouping.groups[0].archive_zip and grouping.groups[0].status == "organized"
    assert warnings and "ZIP 已保留" in warnings[-1]


def test_batch_restore_finalizes_groups_serially_and_deletes_project_zips(
    project, monkeypatch
):
    import sqlite3
    from app.db import db_manager
    from app.services.archive_service import RestoreResult
    from app.services.grouping_service import Group, load_grouping, save_grouping
    from app.workers.batch_restore_worker import (
        BatchRestoreOutcome,
        BatchRestoreSummary,
        BatchRestoreTask,
    )

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db_manager.ensure_schema(db)
    tasks = []
    for index in range(2):
        uid = f"UID-{index}"
        zip_path = project / "results" / f"batch-{index}.zip"
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        zip_path.write_bytes(b"zip-placeholder")
        save_grouping(
            db,
            uid,
            [
                Group(
                    group_index=0,
                    composed_tiff_path=str(project / "results" / f"{uid}.tif"),
                    archive_zip=str(zip_path),
                    jpg_paths=[str(project / "incoming-jpg" / f"{index}.jpg")],
                    status="organized",
                )
            ],
            clean_phantoms=False,
        )
        tasks.append(
            BatchRestoreTask(
                zip_path=str(zip_path),
                mode="original",
                owner_uid=uid,
                group_index=0,
            )
        )

    h = _UndoHarness(str(project), db)
    h._results = types.SimpleNamespace(
        set_batch_restore_running=lambda *a, **k: None,
        clear_result_selection=lambda *a, **k: None,
    )
    h._after_binding_changed = lambda *a, **k: None
    from app.utils import ui as _ui
    monkeypatch.setattr(_ui, "info", lambda *a, **k: None)
    monkeypatch.setattr(_ui, "warn", lambda *a, **k: None)
    summary = BatchRestoreSummary(
        outcomes=[
            BatchRestoreOutcome(
                task=task,
                result=RestoreResult(
                    output_dir=str(project / "incoming-jpg"),
                    restored=[str(project / "incoming-jpg" / f"{index}.jpg")],
                    count=1,
                    ok=True,
                ),
            )
            for index, task in enumerate(tasks)
        ],
        concurrency=2,
    )

    h._on_batch_restore_finished(summary)

    for task in tasks:
        group = load_grouping(db, task.owner_uid).groups[0]
        assert group.status == "composed"
        assert not group.archive_zip
        assert not os.path.exists(task.zip_path)
    assert not (project / "_retired-zip").exists()


# ─────────────────────────────────────────────────────────────────────────────
# 批量还原原片 —— 端到端 (用户 2026-07-12: "我在 results 中选多个 zip, 然后还原
# 原片, 可以批量操作")。worker 层已有并行/失败隔离测试; 这里补的是**结果**:
# 批量跑完后 JPG 真回来了吗 / ZIP 真删了吗 / 组状态真退回了吗 / 失败项的 ZIP 有没有
# 被误删。(Fable 5, 2026-07-12)
# ─────────────────────────────────────────────────────────────────────────────
from PyQt6.QtCore import QObject as _QObject


class _BatchHarness(_QObject, _Harness):
    # BatchRestoreWorker(parent=self) 要求宿主是 QObject —— 真工作台是 QWidget。
    def __init__(self, project_dir: str, db) -> None:
        _QObject.__init__(self)
        _Harness.__init__(self, project_dir)
        self.ctx.get_db = lambda: db
        self.ctx.settings = types.SimpleNamespace(jxl_concurrency=3)
        self._grouping = types.SimpleNamespace(load_grouping=lambda *a, **k: None)
        self._results = types.SimpleNamespace(
            set_batch_restore_running=lambda *a, **k: None,
            clear_result_selection=lambda *a, **k: None,
        )
        self.infos: list = []

    def _after_binding_changed(self, *a, **k) -> None:
        pass

    from app.views.workbench_result_workflow import WorkbenchResultWorkflowMixin as _RW
    _retire_zip = _RW._retire_zip

    def _refresh_results_column(self, *a, **k) -> None:
        pass

    def _refresh_monitor(self, *a, **k) -> None:
        pass


def _owned_zip(project, db, idx: int, *, corrupt: bool = False) -> tuple[str, str, str]:
    """造一个「已整理」组: results/rN.zip + tN.tif + incoming-jpg/PN.jpg 的原路径。"""
    import hashlib
    import json
    from app.services.grouping_service import Group, save_grouping

    uid = f"FJ-XM-A0{idx}-DLC00{idx}-T95E-20260601"
    jpg = project / "incoming-jpg" / f"P{idx}.jpg"
    jpg.parent.mkdir(parents=True, exist_ok=True)
    data = b"\xff\xd8\xff" + bytes([idx]) * 40
    zp = project / "results" / f"r{idx}.zip"
    zp.parent.mkdir(parents=True, exist_ok=True)
    if corrupt:
        zp.write_bytes(b"not a zip at all")          # 解包必失败 → ZIP 必须留着
    else:
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr(f"P{idx}.jpg", data)
            zf.writestr("manifest.json", json.dumps({"files": [{
                "archiveName": f"P{idx}.jpg",
                "originalSize": len(data),
                "originalSha256": hashlib.sha256(data).hexdigest(),
            }]}))
    tif = project / "results" / f"t{idx}.tif"
    tif.write_bytes(b"tif")
    save_grouping(
        db, uid,
        [Group(group_index=0, composed_tiff_path=str(tif), archive_zip=str(zp),
               jpg_paths=[str(jpg)], status="organized")],
        clean_phantoms=False,
    )
    return uid, str(zp), str(jpg)


def test_batch_restore_three_zips_end_to_end(project, monkeypatch, qtbot):
    """勾选 3 个 ZIP → 一次确认 → JPG 全回原位, 3 个 ZIP 全删, TIF 母版全在。"""
    import sqlite3
    from PyQt6.QtWidgets import QMessageBox
    from app.db import db_manager
    from app.services.grouping_service import load_grouping

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db_manager.ensure_schema(db)

    made = [_owned_zip(project, db, i) for i in range(3)]
    for _uid, _zp, jpg in made:
        assert not os.path.isfile(jpg)   # 整理后原片已被 ZIP 消费

    h = _BatchHarness(str(project), db)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    # 收尾弹窗要 QWidget 父窗口, 测试壳是 QObject -> 屏蔽(不影响被测的文件/DB 行为)
    from app.views import workbench_supplementary_workflow as wsw
    monkeypatch.setattr(wsw.ui, "info", lambda *a, **k: None)
    monkeypatch.setattr(wsw.ui, "warn", lambda *a, **k: None)

    # QThread 在测试里同步跑: start() 直接执行 run()
    from app.workers import batch_restore_worker as brw
    monkeypatch.setattr(brw.BatchRestoreWorker, "start", brw.BatchRestoreWorker.run,
                        raising=False)

    h._on_restore_archives_batch([zp for _u, zp, _j in made])

    for uid, zp, jpg in made:
        assert os.path.isfile(jpg), f"{jpg} 应还原回待处理区"
        assert not os.path.isfile(zp), f"{zp} 应在还原成功后删除"
        g = load_grouping(db, uid).groups[0]
        assert not g.archive_zip, "ZIP 登记必须清除"
        assert g.status == "composed", "组退回「已合成、待整理」"
        assert os.path.isfile(g.composed_tiff_path), "TIF 母版不动(红线)"
    db.close()


def test_batch_restore_failed_item_keeps_its_zip(project, monkeypatch, qtbot):
    """一个坏 ZIP + 一个好 ZIP: 好的还原并删除, 坏的**必须保留 ZIP 和归档登记**。"""
    import sqlite3
    from PyQt6.QtWidgets import QMessageBox
    from app.db import db_manager
    from app.services.grouping_service import load_grouping

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db_manager.ensure_schema(db)

    good = _owned_zip(project, db, 0)
    bad = _owned_zip(project, db, 1, corrupt=True)

    h = _BatchHarness(str(project), db)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    from app.views import workbench_supplementary_workflow as wsw
    monkeypatch.setattr(wsw.ui, "info", lambda *a, **k: None)
    monkeypatch.setattr(wsw.ui, "warn", lambda *a, **k: None)
    from app.workers import batch_restore_worker as brw
    monkeypatch.setattr(brw.BatchRestoreWorker, "start", brw.BatchRestoreWorker.run,
                        raising=False)

    h._on_restore_archives_batch([good[1], bad[1]])

    assert os.path.isfile(good[2]) and not os.path.isfile(good[1]), "好的: JPG 回来, ZIP 删除"
    assert os.path.isfile(bad[1]), "坏的: ZIP 必须保留(原片没回来, 归档不能丢)"
    g_bad = load_grouping(db, bad[0]).groups[0]
    assert g_bad.archive_zip, "坏的: 归档登记必须保留"
    assert g_bad.status == "organized", "坏的: 组状态不得退回"
    db.close()
