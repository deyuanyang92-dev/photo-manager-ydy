from app.db import db_manager
from app.services.activity_audit_service import log_event, record_label_print_event


def test_log_event_writes_audit_row(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    conn = db_manager.open_project_db(str(project), create=True)

    row = log_event(
        conn,
        actor="张三",
        action="specimen.update",
        entity_type="specimen",
        entity_id="UID-1",
        new_value={"field": "storage"},
    )

    assert row["actor"] == "张三"
    assert row["action"] == "specimen.update"
    assert row["entity_type"] == "specimen"
    assert row["entity_id"] == "UID-1"


def test_record_label_print_event_writes_print_and_audit_rows(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    conn = db_manager.open_project_db(str(project), create=True)

    event = record_label_print_event(
        conn,
        specimen_uids=["UID-1", "UID-2"],
        actor="李四",
        bucket="tissue",
        template_key="tissueCompact",
        printer_name="TubePrinter",
        copies=3,
    )

    assert event["actor"] == "李四"
    assert event["bucket"] == "tissue"
    assert event["label_count"] == 6
    audit = conn.execute(
        "SELECT * FROM audit_log WHERE entity_type='label_print_event'"
    ).fetchone()
    assert audit["actor"] == "李四"
    assert audit["action"] == "label.print"
    assert audit["entity_id"] == event["event_id"]
