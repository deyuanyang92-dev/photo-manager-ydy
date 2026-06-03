# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Cross-platform (Windows/macOS/Linux) **PyQt6 desktop port** of a validated web prototype
(`/mnt/n/claude/photo-platform-ydy/prototype-photo-gui/` — `app.js` ~18.5K lines, `server.js`
107 endpoints, 14 modules) for a specimen-photo workbench. The web prototype is the **behavioral
oracle**: ported logic must mirror the named JS file/line ranges exactly, not be reinvented.
Goal is faithful behavior + pleasant UI, **not pixel-perfect QSS replication** (see ADR 0001).

Model workflow: Opus writes a structured spec in `docs/specs/` → Sonnet implements it TDD →
an independent Opus does acceptance. Specs are the implementer's *only* input — if a spec is
self-contradictory or impossible, **stop and report; do not redesign unilaterally**.

## Commands

```bash
pip install -r requirements.txt

python main.py                                   # normal launch (needs a display)
QT_QPA_PLATFORM=xcb python main.py               # WSL2/WSLg: force X11 — Wayland socket is flaky
                                                 #   ("Failed to create wl_display"); window shows
                                                 #   on the Windows desktop via WSLg, no browser.
QT_QPA_PLATFORM=offscreen python main.py         # headless smoke check (CI / WSL)

pytest tests/ -v                                 # full suite
pytest tests/test_import_service.py -v           # one file
pytest tests/test_naming_uid.py::test_<name> -v  # one test
QT_QPA_PLATFORM=offscreen pytest tests/ -v       # view/widget tests headless (pytest-qt)
```

External CLIs are detected at runtime, never bundled-by-default: `cjxl`/`djxl` (libjxl-tools /
brew jpeg-xl), Helicon Focus (detected only, never distributed). Their absence must degrade
gracefully, never crash.

## Architecture

**DI + view registry shell.** `main.py` builds one `AppContext` (the single dependency-injection
container: settings + current project dir + DB access) and one `MainWindow`, then registers every
class in `app/views/registry.py::ALL_VIEWS`. Views never import each other — they reach shared
state only through `ctx`.

- `app/app_context.py` — `AppContext`; `ctx.get_db()` returns the SQLite connection for the
  current project (or `None` if no project loaded).
- `app/main_window.py` — `QMainWindow` shell: top-bar segmented nav + context bar + bottom
  `QStatusBar`, with a `QStackedWidget` holding one page per view. `register_view(cls)` builds the
  view eagerly so stack index == nav order. Nav order = web prototype topbar order.
- `app/views/base_view.py` — every page subclasses `BaseView`, defining class attrs
  `view_id` (snake_case, unique, used as objectName), `nav_title`, `nav_icon`, building UI in
  `_setup_ui()`, and overriding `on_activate()` (called on every page entry; runs on main thread).
- `app/views/` pages, `app/widgets/` reusable panels, `app/services/` (14) = the ported business
  logic from the web JS modules, `app/models/`, `app/utils/`, `app/config/`.

**Per-project SQLite is the source of truth.** Each project directory gets its own
`<project>/_data/project.db` (`app/db/db_manager.py`): `open_project_db()` resolves the path,
sets WAL + `foreign_keys=ON`, runs idempotent `ensure_schema()`, and **caches the connection by
resolved path**. Schema in `app/db/schema.sql`; `darwin_core` is a VIEW (re-created via
`DROP VIEW IF EXISTS`). Every table carries a `raw_json` column holding the full original object —
the zero-field-loss fallback.

**Theme.** `app/config/theme.py` holds the deep-teal design tokens; `build_theme_qss_file()`
generates `resources/theme.qss`, loaded as the app stylesheet in `main.py`.

## Hard red lines (never violate — these are the reason the project exists)

1. **TIFF is never deleted.**
2. **JPG deletion requires all four preconditions** (`app/services/archive_service.py`, oracle
   `archive.js:28-61`): cjxl available + ZIP exists (>32 B) + `verify_manifest_complete` +
   `verify_jxl_recoverable` (djxl actually re-decodes each JXL). **Default delete_jpg=False.**
   If djxl is missing, check (d) fails → JPGs are NOT deleted.
3. **Import is strictly read-only** (`app/services/import_service.py`): source `data/*.json` is
   sha256-snapshotted before and re-verified after; any change raises `IntegrityError`. Corrupt
   JSON aborts with no partial writes. Per-row `INSERT OR REPLACE` (idempotent), not
   "skip if table non-empty".
4. **cjxl flags are exactly `--distance 0 -e <effort>`** (lossless bit-exact). Never
   `--quality`/`--modular`/`-j` (oracle `compress.js:32-39`).
5. Path safety = stateful `SafePathRegistry`, `..` checked via `relative_to` (oracle
   `server.js:83-102`).

## Domain gotchas (from real data — getting these wrong has burned prior ports)

- **No `species`/`species_cn` columns exist.** Chinese name lives in `scientific_name_cn`; the Latin
  species name in `scientific_name`; common name only in `raw_json`. Do not add those columns.
- **UID derivation** (`app/utils/naming.py`, oracle `db-utils.js:121-122,158-165`):
  `[province, site, station, id, storage, dateSeg]` filtered of falsy → joined by `-`. Missing
  `station` auto-degrades to one fewer segment — that is correct, not a bug.
- **Chinese task keys are preserved verbatim** as the tasks PK (e.g. `浙江-三门湾-B2-...`) — the
  JSON key is used raw, never parsed/validated, or those rows get silently dropped.
- JPG attribution uses `firstSeenAt` (persisted in `seen_files`), **not** file mtime
  (oracle `monitor-service.js:101-116`).
- Empty `lon`/`lat` strings store as NULL, not 0.

## Conventions

- All new code is TDD: write a failing test (incl. a contract/invariant test for any red-line
  behavior), confirm red, implement, confirm green, commit. Don't mock away sha256 / safety gates.
- Module views: subclass `BaseView`, add to `ALL_VIEWS`; `MainWindow` wires nav + stack.
- UI text is Chinese-first. Commits follow Conventional Commits (`feat(scope): ...`), Chinese
  subjects are the norm here.
- `docs/adr/` = accepted decisions; `docs/specs/` = per-module implementation specs;
  `docs/shots/` = web-vs-Qt comparison screenshots (capture scripts alongside).

## UI design freeze

Existing UI layout / visual style / confirmed UX flows must not be changed without an explicit
user instruction in the current conversation. Fix functional bugs without altering appearance
unless the user asks otherwise. (Mirrors the global rule in `~/.claude/CLAUDE.md`.)
