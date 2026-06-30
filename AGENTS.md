# Repository Guidelines

## Project Structure & Module Organization

This is a Python/PyQt6 desktop application for specimen photo workflow management.

- `main.py` starts the GUI.
- `app/` contains application code: `views/` for pages, `widgets/` for reusable UI, `services/` for domain logic, `workers/` for background jobs, `models/`, `db/`, `utils/`, and `config/`.
- `tests/` contains pytest and pytest-qt coverage.
- `resources/` stores branding, i18n, and geo assets.
- `data/` holds local taxonomy/cache data.
- `docs/` contains ADRs, specs, audits, and screenshot helpers.
- `scripts/` contains build and maintenance utilities.

## Build, Test, and Development Commands

```bash
pip install -r requirements.txt
python main.py
QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q
python -m pytest tests/test_archive_service.py -q
```

- `pip install` installs PyQt6, image, export, web-service, and test dependencies.
- `python main.py` runs the desktop app locally.
- Use `QT_QPA_PLATFORM=offscreen` for GUI tests in headless shells.
- On Windows, build the portable package with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

## Coding Style & Naming Conventions

Use standard Python style with 4-space indentation. Prefer `snake_case` for functions, methods, and variables; `PascalCase` for classes; and descriptive module names such as `archive_service.py` or `grouping_panel.py`. Keep UI behavior in `views/` or `widgets/`; keep file/database/business rules in `services/`. Add comments only where they clarify non-obvious workflow or data-safety rules.

## Testing Guidelines

Tests use `pytest` with `pytest-qt`; `pytest.ini` pins `qt_api = pyqt6` and sets a 30-second timeout. Name tests `test_*.py` and keep test classes/methods behavior-focused. For UI changes, include focused tests in the relevant `tests/test_*_view.py` or widget test file. Run targeted tests before broad suites.

## Commit & Pull Request Guidelines

Recent history uses concise conventional-style subjects such as `feat(workbench): ...`, `fix(collection-records): ...`, and `release: v0.01`. Follow that pattern: start with `feat`, `fix`, `docs`, `test`, `refactor`, or `release`, with an optional scope.

PRs should explain the user-visible change, list verification commands, link relevant issues/specs, and include screenshots for visible UI changes.

## Data Safety & Configuration

Never auto-delete TIFF originals. JPG deletion must only occur after archive integrity checks pass. Avoid committing large generated build outputs unless intentionally updating a release package.

## Workbench Selected-JPG Rule

Read `docs/PROJECT_MEMORY.md` before changing core workflow behavior. It records user-repeated requirements that must not be rediscovered through chat.

Selected JPG compose/organise is a manual workflow and must not require an active specimen number. If an active UID exists, compose selected JPGs under that active UID and auto-name the next result sequence. If no active UID exists, prompt for either a target UID or a free output stem; target UID assigns/moves them and auto-names, free stem names both TIF and ZIP. Existing JPG attribution is only a hint when no UID is active.

The detailed business rule is in `docs/specs/photo-grouping-workflow.md`. Do not reintroduce "请先激活编号" for selected-JPG `合成` or `合成+整理`.
