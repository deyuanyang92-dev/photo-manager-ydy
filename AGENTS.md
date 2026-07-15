# Repository Guidelines

## Mandatory Grilling Before Changes

- For every requirement, design, feature, bug-fix, or code-change request, use the installed `grill-with-docs` skill with its `grilling` and `domain-modeling` workflows before acting. Explore facts from the repository instead of asking the user questions that the code can answer. Ask only one unresolved decision question at a time, include a recommended answer, and do not implement until shared understanding is confirmed. Keep `CONTEXT.md` limited to stable domain terminology, and create ADRs only for hard-to-reverse decisions with real trade-offs.
- Direct operational commands such as continue, restart, close, run tests, or report status should be executed directly without unnecessary questioning.

## Project Structure & Module Organization

This is a Python/PyQt6 desktop app for specimen photo workflow management.

- `main.py` starts the GUI.
- `app/` contains code: `views/` pages, `widgets/` reusable UI, `services/` business/file rules, `workers/` background jobs, plus `models/`, `db/`, `utils/`, and `config/`.
- `tests/` contains pytest and pytest-qt coverage.
- `resources/` stores branding, i18n, and geo assets.
- `data/` holds local taxonomy and cache data.
- `docs/` contains ADRs, specs, audits, project memory, and screenshot helpers.
- `scripts/` contains build and maintenance utilities.

## Build, Test, and Development Commands

```bash
pip install -r requirements.txt
python main.py
QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q
python -m pytest tests/test_archive_service.py -q
```

- `pip install -r requirements.txt` installs runtime and test dependencies.
- `python main.py` runs the desktop app locally.
- Use `QT_QPA_PLATFORM=offscreen` for GUI tests in headless shells.

For Windows portable builds:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

## Coding Style & Naming Conventions

Use standard Python style with 4-space indentation. Prefer `snake_case` for functions, methods, and variables; `PascalCase` for classes; and descriptive module names such as `archive_service.py`. Keep UI behavior in `views/` or `widgets/`; keep file, database, and workflow rules in `services/`. Add comments only for non-obvious workflow or data-safety constraints.

## Testing Guidelines

Tests use `pytest` with `pytest-qt`; `pytest.ini` pins `qt_api = pyqt6` and sets a 30-second timeout. Name files `test_*.py` and keep cases behavior-focused. For UI changes, add focused coverage in the relevant view or widget test, such as `tests/test_grouping_panel.py`.

## Commit & Pull Request Guidelines

Recent history uses concise conventional-style subjects such as `release: v0.6`, `feat(workbench): ...`, and `fix(workbench): ...`. Start commits with `feat`, `fix`, `docs`, `test`, `refactor`, or `release`; add an optional scope.

PRs should describe user-visible changes, list verification commands, link issues or specs, and include screenshots for visible UI changes.

## Data Safety & Workflow Rules

Never auto-delete TIFF originals. Delete JPGs only after archive integrity checks pass. Avoid committing large generated outputs unless updating a release package.

Before changing core workflow behavior, read `docs/PROJECT_MEMORY.md`. Selected JPG compose/organise is manual and must not require an active specimen number. With an active UID, compose under it and auto-name the next result sequence. Without one, prompt for a target UID or free output stem. Do not reintroduce "请先激活编号" for selected-JPG `合成` or `合成+整理`; see `docs/specs/photo-grouping-workflow.md`.

## Imported Claude Cowork project instructions
