#!/usr/bin/env python3
"""Run focused regression suites for high-risk workflow changes.

Usage examples:
    python scripts/run_core_regression.py
    python scripts/run_core_regression.py naming
    python scripts/run_core_regression.py workbench compose
    python scripts/run_core_regression.py --list
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

Command = tuple[str, ...]


SUITES: dict[str, list[Command]] = {
    "quick": [
        ("-m", "compileall", "-q", "app", "scripts"),
        (
            "-m",
            "pytest",
            "tests/test_project_settings_service.py",
            "tests/test_project_settings_drawer.py",
            "tests/test_naming_panel.py",
            "tests/test_workbench_view.py::TestProjectSettingsDrawer",
            "tests/test_workbench_view.py::TestRightRailSpecimenIdentityEdits",
            "tests/test_tiff_naming_service.py",
            "tests/test_naming_full.py",
            "-q",
        ),
    ],
    "naming": [
        (
            "-m",
            "pytest",
            "tests/test_project_settings_service.py",
            "tests/test_project_settings_drawer.py",
            "tests/test_naming_panel.py",
            "tests/test_naming_uid.py",
            "tests/test_naming_full.py",
            "tests/test_tiff_naming_service.py",
            "-q",
        ),
    ],
    "workbench": [
        (
            "-m",
            "pytest",
            "tests/test_workbench_view.py::TestRightRailSpecimenIdentityEdits",
            "tests/test_workbench_view.py::TestImplicitCompose",
            "tests/test_workbench_view.py::TestAdhocGrouping",
            "tests/test_workbench_view.py::TestComposePreviewDialog",
            "tests/test_workbench_wiring.py",
            "tests/test_monitor_panel.py::TestSelectionAccessors",
            "tests/test_monitor_panel.py::TestSelectionAddToGroup",
            "tests/test_monitor_panel.py::TestComposePreviewToggle",
            "-q",
        ),
    ],
    "compose": [
        (
            "-m",
            "pytest",
            "tests/test_compose_workflow_service.py",
            "tests/test_organize_workflow_service.py",
            "tests/test_organize_service.py",
            "tests/test_workbench_view.py::TestComposePreviewDialog",
            "tests/test_workbench_view.py::TestImplicitCompose",
            "tests/test_workbench_view.py::TestBatchComposeOrganise",
            "tests/test_workbench_view.py::TestAutoOrganizeAfterCompose",
            "tests/test_workbench_view.py::TestUndoComposeDeletesTiff",
            "-q",
        ),
    ],
    "labels": [
        (
            "-m",
            "pytest",
            "tests/test_label_core.py",
            "tests/test_label_print.py",
            "tests/test_label_print_batch.py",
            "tests/test_label_print_executor.py",
            "tests/test_labels_view.py::TestPrintBatch",
            "tests/test_labels_view.py::TestPrintSettingsDirectMode",
            "tests/test_labels_view.py::TestPrintBothButton",
            "tests/test_label_imposition_dialog.py",
            "tests/test_label_imposition_persist.py",
            "-q",
        ),
    ],
    "collection": [
        (
            "-m",
            "pytest",
            "tests/test_collection_autofill.py",
            "tests/test_collection_record_service.py",
            "tests/test_collection_records_view.py",
            "tests/test_collection_records_grid.py",
            "-q",
        ),
    ],
    "collab": [
        (
            "-m",
            "pytest",
            "tests/test_app_settings_collab.py",
            "tests/test_collab_status.py",
            "tests/test_collab_service.py",
            "tests/test_collab_view.py",
            "tests/test_collab_setup_wizard.py",
            "tests/test_collab_pairing.py",
            "tests/test_collab_group_sync.py",
            "tests/test_collab_file_sync.py",
            "tests/test_main_window.py::test_collab_status_bar_uses_shared_status",
            "tests/test_settings_view.py::TestCollabTab",
            "-q",
        ),
    ],
}

ALL_CORE_ORDER = ("quick", "workbench", "compose", "labels", "collection", "collab")


def _expand_suites(names: list[str]) -> list[str]:
    expanded: list[str] = []
    for name in names:
        if name == "all-core":
            expanded.extend(ALL_CORE_ORDER)
        else:
            expanded.append(name)
    return expanded


def _commands_for(names: list[str]) -> list[Command]:
    commands: list[Command] = []
    seen: set[Command] = set()
    for name in _expand_suites(names):
        for command in SUITES[name]:
            if command not in seen:
                seen.add(command)
                commands.append(command)
    return commands


def _run(command: Command, *, dry_run: bool) -> int:
    cmd = (sys.executable, *command)
    printable = " ".join(cmd)
    print(f"\n==> {printable}", flush=True)
    if dry_run:
        return 0
    env = os.environ.copy()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    started = time.monotonic()
    result = subprocess.run(cmd, cwd=ROOT, env=env)
    elapsed = time.monotonic() - started
    if result.returncode == 0:
        print(f"OK ({elapsed:.1f}s)", flush=True)
    else:
        print(f"FAILED ({elapsed:.1f}s): exit {result.returncode}", flush=True)
    return int(result.returncode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run focused regression suites for the photo workflow app."
    )
    parser.add_argument(
        "suites",
        nargs="*",
        choices=sorted([*SUITES.keys(), "all-core"]),
        help="Regression suite(s) to run. Default: quick.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available suites and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without running them.",
    )
    args = parser.parse_args(argv)

    if args.list:
        print("Available suites:")
        for name in sorted(SUITES):
            print(f"  {name}")
        print("  all-core")
        return 0

    failed = 0
    suites = args.suites or ["quick"]
    for command in _commands_for(suites):
        failed = _run(command, dry_run=args.dry_run)
        if failed:
            break
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
