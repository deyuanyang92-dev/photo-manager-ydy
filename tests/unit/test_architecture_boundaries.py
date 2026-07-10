"""Static guardrails for the gradual architecture cleanup."""

from __future__ import annotations

import ast
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _REPO_ROOT / "app"

_ALLOWED_SERVICE_UI_IMPORTS = {
    ("app/services/label_print_executor.py", "app.widgets.print_dialog"),
}

_KNOWN_UI_DB_FILES = {
    "app/views/data_filter_view.py",
    "app/views/overview_view.py",
    "app/views/project_tree_view.py",
    "app/views/summary_view.py",
    "app/views/workbench_result_workflow.py",
    "app/views/workbench_view.py",
    "app/widgets/coord_import_dialog.py",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def _relative(path: Path) -> str:
    return path.relative_to(_REPO_ROOT).as_posix()


def test_service_layer_does_not_gain_ui_dependencies() -> None:
    violations: set[tuple[str, str]] = set()
    for path in (_APP_ROOT / "services").rglob("*.py"):
        for imported in _imports(path):
            if imported.startswith(("app.views", "app.widgets")):
                violations.add((_relative(path), imported))

    assert violations <= _ALLOWED_SERVICE_UI_IMPORTS


def test_direct_ui_database_access_does_not_spread() -> None:
    direct_db_files: set[str] = set()
    for layer in ("views", "widgets"):
        for path in (_APP_ROOT / layer).rglob("*.py"):
            imports = _imports(path)
            if "sqlite3" in imports or any(name.startswith("app.db") for name in imports):
                direct_db_files.add(_relative(path))

    assert direct_db_files <= _KNOWN_UI_DB_FILES
