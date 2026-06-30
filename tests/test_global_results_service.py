from pathlib import Path

from app.db import db_manager
from app.services import global_results_service as grs
from app.services.global_results_service import (
    clear_global_results_cache,
    collect_global_result_ledger,
    summarize_ledger,
)


def _workspace(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "results").mkdir()
    db = db_manager.open_project_db(str(root), create=True)
    db.execute(
        """
        INSERT INTO specimens
          (uid, scientific_name, storage, collection_date, photo_date, owner_project_dir)
        VALUES
          (?, 'Marphysa sp.', 'D95E', '20260618', '20260618', ?)
        """,
        (f"{name.upper()}-UID-1", str(root)),
    )
    db.commit()
    return root


def test_collects_registered_tiff_zip_pair(tmp_path):
    root = _workspace(tmp_path, "alpha")
    db = db_manager.get_db(str(root))
    tif = root / "results" / "ALPHA-UID-1-1.tif"
    zipf = root / "results" / "ALPHA-UID-1-1.zip"
    tif.write_bytes(b"tif")
    zipf.write_bytes(b"zip")
    db.execute(
        """
        INSERT INTO grouping
          (uid, group_index, composed_tiff_path, archive_zip, status)
        VALUES (?, 0, ?, ?, 'organized')
        """,
        ("ALPHA-UID-1", str(tif), str(zipf)),
    )
    db.commit()

    rows = collect_global_result_ledger(workspace_dirs=[str(root)], include_empty_specimens=False)

    assert len(rows) == 1
    assert rows[0].status == "完整"
    assert rows[0].registered is True
    assert rows[0].has_specimen is True
    assert rows[0].tiff_exists is True
    assert rows[0].zip_exists is True


def test_reports_specimen_without_results(tmp_path):
    root = _workspace(tmp_path, "beta")

    rows = collect_global_result_ledger(workspace_dirs=[str(root)])

    assert len(rows) == 1
    assert rows[0].display_uid == "BETA-UID-1"
    assert rows[0].status == "无成果"


def test_reports_missing_registered_zip_file(tmp_path):
    root = _workspace(tmp_path, "gamma")
    db = db_manager.get_db(str(root))
    tif = root / "results" / "GAMMA-UID-1-1.tif"
    zipf = root / "results" / "GAMMA-UID-1-1.zip"
    tif.write_bytes(b"tif")
    db.execute(
        """
        INSERT INTO grouping
          (uid, group_index, composed_tiff_path, archive_zip, status)
        VALUES (?, 0, ?, ?, 'organized')
        """,
        ("GAMMA-UID-1", str(tif), str(zipf)),
    )
    db.commit()

    rows = collect_global_result_ledger(workspace_dirs=[str(root)], include_empty_specimens=False)

    assert rows[0].status == "缺 ZIP文件"
    assert rows[0].zip_exists is False


def test_scans_orphan_result_files_and_infers_uid(tmp_path):
    root = _workspace(tmp_path, "delta")
    tif = root / "results" / "DELTA-UID-1-extra.tif"
    zipf = root / "results" / "DELTA-UID-1-extra.zip"
    tif.write_bytes(b"tif")
    zipf.write_bytes(b"zip")

    rows = collect_global_result_ledger(workspace_dirs=[str(root)], include_empty_specimens=False)

    assert len(rows) == 1
    assert rows[0].status == "未入库"
    assert rows[0].registered is False
    assert rows[0].orphan is True
    assert rows[0].inferred_uid == "DELTA-UID-1"
    assert rows[0].has_specimen is True


def test_summarize_ledger_counts_review_work(tmp_path):
    root = _workspace(tmp_path, "epsilon")
    tif = root / "results" / "unmatched.tif"
    tif.write_bytes(b"tif")

    rows = collect_global_result_ledger(workspace_dirs=[str(root)])
    counts = summarize_ledger(rows)

    assert counts["specimens"] == 1
    assert counts["tiffs"] == 1
    assert counts["orphan"] == 1


def test_collect_global_result_ledger_uses_cache(monkeypatch, tmp_path):
    root = _workspace(tmp_path, "zeta")
    clear_global_results_cache()
    rows = collect_global_result_ledger(workspace_dirs=[str(root)])
    assert rows

    def fail_collect(*_args, **_kwargs):
        raise AssertionError("workspace scan should be cached")

    monkeypatch.setattr(grs, "_collect_workspace", fail_collect)
    cached = collect_global_result_ledger(workspace_dirs=[str(root)])
    assert cached == rows


def test_collect_global_result_ledger_can_bypass_cache(monkeypatch, tmp_path):
    root = _workspace(tmp_path, "eta")
    clear_global_results_cache()
    collect_global_result_ledger(workspace_dirs=[str(root)])

    def fail_collect(*_args, **_kwargs):
        raise RuntimeError("forced refresh")

    monkeypatch.setattr(grs, "_collect_workspace", fail_collect)
    try:
        collect_global_result_ledger(workspace_dirs=[str(root)], use_cache=False)
    except RuntimeError as exc:
        assert str(exc) == "forced refresh"
    else:
        raise AssertionError("use_cache=False should bypass cached ledger")
