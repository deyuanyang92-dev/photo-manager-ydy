import pytest

from app.db import db_manager


@pytest.fixture(autouse=True)
def close_dbs():
    db_manager.close_all()
    yield
    db_manager.close_all()


def _db(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    return db_manager.open_project_db(str(project), create=True)


def test_reserve_block_advances_sequence(tmp_path):
    from app.services.uid_sequence_service import reserve_block

    db = _db(tmp_path)
    r1 = reserve_block(db, "dlc", count=5, device_id="dev-a")
    r2 = reserve_block(db, "DLC", count=3, device_id="dev-b")

    assert r1["start_number"] == 1
    assert r1["end_number"] == 5
    assert r2["start_number"] == 6
    assert r2["end_number"] == 8


def test_consume_next_formats_species_id_and_exhausts(tmp_path):
    from app.services.uid_sequence_service import consume_next, reserve_block

    db = _db(tmp_path)
    r = reserve_block(db, "gx", count=2, padding=4)

    assert consume_next(db, r["reservation_id"]) == "GX0001"
    assert consume_next(db, r["reservation_id"]) == "GX0002"
    with pytest.raises(ValueError):
        consume_next(db, r["reservation_id"])


def test_reserve_and_consume_online_host_mode(tmp_path):
    from app.services.uid_sequence_service import reserve_and_consume

    db = _db(tmp_path)
    assert reserve_and_consume(db, "DLC") == "DLC001"
    assert reserve_and_consume(db, "DLC") == "DLC002"


def test_ensure_device_upserts_last_seen(tmp_path):
    from app.services.uid_sequence_service import ensure_device

    db = _db(tmp_path)
    first = ensure_device(db, device_id="dev-1", device_name="Laptop A", owner="A")
    second = ensure_device(db, device_id="dev-1", device_name="Laptop A2", owner="A")

    assert first["device_id"] == "dev-1"
    assert second["device_name"] == "Laptop A2"
    assert db.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 1
