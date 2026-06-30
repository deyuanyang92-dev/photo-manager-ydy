"""Tests for application diagnostics and rotating file logging."""
from __future__ import annotations

import logging


def test_setup_logging_writes_to_env_log_dir(monkeypatch, tmp_path):
    from app.utils import diagnostics
    from logging.handlers import RotatingFileHandler

    diagnostics.reset_for_tests()
    monkeypatch.setenv("SPECIMEN_WORKBENCH_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(diagnostics, "RotatingFileHandler", RotatingFileHandler)
    try:
        path = diagnostics.setup_logging(debug=True)
        logging.getLogger("tests.diagnostics").debug("diagnostic log marker")
        diagnostics.flush_logs()

        assert path == tmp_path / "app.log"
        assert "diagnostic log marker" in path.read_text(encoding="utf-8")
    finally:
        diagnostics.reset_for_tests()


def test_setup_logging_falls_back_when_log_file_cannot_open(monkeypatch, tmp_path):
    from app.utils import diagnostics
    from logging.handlers import RotatingFileHandler

    diagnostics.reset_for_tests()
    monkeypatch.setenv("SPECIMEN_WORKBENCH_LOG_DIR", str(tmp_path))
    calls = []

    def flaky_handler(path, *args, **kwargs):
        calls.append(path)
        if len(calls) == 1:
            raise OSError("log path unavailable")
        return RotatingFileHandler(path, *args, **kwargs)

    monkeypatch.setattr(diagnostics, "RotatingFileHandler", flaky_handler)
    try:
        path = diagnostics.setup_logging()

        assert calls == [tmp_path / "app.log", path]
        assert path.name == "app.log"
        assert path.parent.name == "specimen-photo-workbench-logs"
    finally:
        diagnostics.reset_for_tests()


def test_format_diagnostic_includes_support_context(monkeypatch, tmp_path):
    from app.utils import diagnostics
    from logging.handlers import RotatingFileHandler

    diagnostics.reset_for_tests()
    monkeypatch.setenv("SPECIMEN_WORKBENCH_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(diagnostics, "RotatingFileHandler", RotatingFileHandler)
    try:
        diagnostics.setup_logging()
        payload = diagnostics.format_diagnostic(
            "程序遇到错误",
            "boom",
            detail="Traceback line",
            context={"project": "/data/project"},
        )

        assert "Title: 程序遇到错误" in payload
        assert "Message: boom" in payload
        assert f"Log: {tmp_path / 'app.log'}" in payload
        assert "project: /data/project" in payload
        assert "Traceback line" in payload
    finally:
        diagnostics.reset_for_tests()
