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

    def __init__(self, project_dir: str) -> None:
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
