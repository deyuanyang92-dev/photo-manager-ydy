"""Tests for application diagnostics and rotating file logging."""
from __future__ import annotations

import logging
from types import SimpleNamespace


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
        assert f"Native crash log: {tmp_path / 'crash.log'}" in payload
        assert "project: /data/project" in payload
        assert "Traceback line" in payload
    finally:
        diagnostics.reset_for_tests()


def test_install_runtime_diagnostics_routes_faults_and_thread_errors(
    monkeypatch, tmp_path, caplog
):
    from app.utils import diagnostics

    diagnostics.reset_for_tests()
    monkeypatch.setenv("SPECIMEN_WORKBENCH_LOG_DIR", str(tmp_path))
    enabled = {}
    previous_calls = []
    disabled = []
    monkeypatch.setattr(
        diagnostics.faulthandler,
        "enable",
        lambda **kwargs: enabled.update(kwargs),
    )
    monkeypatch.setattr(
        diagnostics.faulthandler,
        "disable",
        lambda: disabled.append(True),
    )
    monkeypatch.setattr(
        diagnostics.threading,
        "excepthook",
        lambda args: previous_calls.append(args),
    )
    try:
        path = diagnostics.install_runtime_diagnostics()
        hook = diagnostics.threading.excepthook
        exc = RuntimeError("worker boom")
        args = SimpleNamespace(
            exc_type=RuntimeError,
            exc_value=exc,
            exc_traceback=exc.__traceback__,
            thread=SimpleNamespace(name="worker-1"),
        )
        with caplog.at_level(logging.CRITICAL, logger="app.thread"):
            hook(args)

        assert path == tmp_path / "crash.log"
        assert enabled["all_threads"] is True
        assert enabled["file"].name == str(path)
        assert "worker-1" in caplog.text
        assert previous_calls == [args]
    finally:
        diagnostics.reset_for_tests()
        assert disabled
