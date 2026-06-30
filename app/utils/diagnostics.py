"""Application diagnostics and file logging helpers."""
from __future__ import annotations

import logging
import os
import platform
import sys
import tempfile
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Mapping

_APP_DIR_WINDOWS = "SpecimenPhotoWorkbench"
_APP_DIR_POSIX = "specimen-photo-workbench"
_LOG_FILE = "app.log"
_HANDLER_MARK = "_specimen_photo_workbench_handler"
_LOG_PATH: Path | None = None
_PREVIOUS_ROOT_LEVEL: int | None = None


def _ensure_dir(path: Path) -> Path:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        fallback = Path(tempfile.gettempdir()) / f"{_APP_DIR_POSIX}-logs"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def log_dir() -> Path:
    """Return the writable directory used for application logs."""
    override = os.environ.get("SPECIMEN_WORKBENCH_LOG_DIR", "").strip()
    if override:
        return _ensure_dir(Path(override).expanduser())

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return _ensure_dir(root / _APP_DIR_WINDOWS / "logs")

    if sys.platform == "darwin":
        return _ensure_dir(Path.home() / "Library" / "Logs" / _APP_DIR_WINDOWS)

    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".local" / "state"
    return _ensure_dir(root / _APP_DIR_POSIX / "logs")


def log_path() -> Path:
    """Return the current log file path without reconfiguring logging."""
    return _LOG_PATH or (log_dir() / _LOG_FILE)


def setup_logging(debug: bool | None = None) -> Path:
    """Configure process-wide rotating file logging and return the log path."""
    global _LOG_PATH, _PREVIOUS_ROOT_LEVEL

    path = log_dir() / _LOG_FILE

    env_debug = os.environ.get("SPECIMEN_WORKBENCH_DEBUG", "").lower()
    debug_enabled = debug if debug is not None else env_debug in {"1", "true", "yes", "on"}
    level = logging.DEBUG if debug_enabled else logging.INFO

    root = logging.getLogger()
    if _PREVIOUS_ROOT_LEVEL is None:
        _PREVIOUS_ROOT_LEVEL = root.level
    root.setLevel(level)

    existing = [
        handler for handler in root.handlers
        if getattr(handler, _HANDLER_MARK, False)
    ]
    if existing:
        for handler in existing:
            handler.setLevel(level)
        return _LOG_PATH or path

    try:
        handler = RotatingFileHandler(
            path,
            maxBytes=3_000_000,
            backupCount=5,
            encoding="utf-8",
        )
    except OSError:
        path = _ensure_dir(Path(tempfile.gettempdir()) / f"{_APP_DIR_POSIX}-logs") / _LOG_FILE
        handler = RotatingFileHandler(
            path,
            maxBytes=3_000_000,
            backupCount=5,
            encoding="utf-8",
        )
    _LOG_PATH = path
    setattr(handler, _HANDLER_MARK, True)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    ))
    root.addHandler(handler)
    logging.getLogger(__name__).info("Logging initialized at %s", path)
    return path


def format_diagnostic(
    title: str,
    message: str,
    detail: str = "",
    context: Mapping[str, object] | None = None,
) -> str:
    """Build a copyable bug report payload for dialogs and support requests."""
    lines = [
        f"Title: {title}",
        f"Message: {message}",
        f"Time: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"Log: {log_path()}",
        f"Python: {platform.python_version()} ({sys.executable})",
        f"Platform: {platform.platform()}",
        f"Working directory: {Path.cwd()}",
        f"Process ID: {os.getpid()}",
    ]
    if context:
        lines.append("Context:")
        for key, value in context.items():
            if value not in (None, ""):
                lines.append(f"  {key}: {value}")
    if detail:
        lines.extend(["", "Detail:", detail.rstrip()])
    return "\n".join(lines).rstrip() + "\n"


def flush_logs() -> None:
    """Flush application log handlers; useful before tests inspect the file."""
    for handler in logging.getLogger().handlers:
        if getattr(handler, _HANDLER_MARK, False):
            handler.flush()


def reset_for_tests() -> None:
    """Remove diagnostics handlers so tests can isolate log directories."""
    global _LOG_PATH, _PREVIOUS_ROOT_LEVEL
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            root.removeHandler(handler)
            handler.close()
    if _PREVIOUS_ROOT_LEVEL is not None:
        root.setLevel(_PREVIOUS_ROOT_LEVEL)
    _LOG_PATH = None
    _PREVIOUS_ROOT_LEVEL = None
