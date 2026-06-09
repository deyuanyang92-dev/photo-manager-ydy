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
- `app/views/` pages, `app/widgets/` reusable panels, `app/services/` = the ported business
  logic from the web JS modules, `app/models/`, `app/utils/`, `app/config/`, `app/workers/`.

**Per-project SQLite is the source of truth.** Each project directory gets its own
`<project>/_data/project.db` (`app/db/db_manager.py`): `open_project_db()` resolves the path,
sets WAL + `foreign_keys=ON`, runs idempotent `ensure_schema()`, and **caches the connection by
resolved path**. Schema in `app/db/schema.sql`; `darwin_core` is a VIEW (re-created via
`DROP VIEW IF EXISTS`). Every table carries a `raw_json` column holding the full original object —
the zero-field-loss fallback.

**Cross-view communication.** Views never call each other. Handoffs use ad-hoc attributes set
directly on `ctx`: e.g. `ctx.pending_label_uid` (workbench → labels), `ctx.worms_fill_specimen`
(workbench → worms). The receiving view reads and clears these in `on_activate()`. This is
intentional — not a bug to "clean up" into signals.

**Workers.** Heavy background ops live in `app/workers/` (`HeliconWorker`) or as inner
`QThread`/`QObject` subclasses in the view file that owns them (WoRMS search workers,
`_NominatimWorker`, collab threads). All use Qt signals for thread-safe result delivery — never
touch UI widgets from worker threads.

**Theme.** `app/config/theme.py` holds multi-theme design tokens (`TOKENS` dict, updated
in-place by `apply_theme(name)`). `build_theme_qss_file()` writes `resources/theme.qss`, loaded
as the app stylesheet in `main.py`. Views that need theme-reactive repaint implement
`_refresh_palette()` called from `on_activate()`.

**UI helpers.** Always use `app/utils/ui.py` for file dialogs and message boxes — it forces
`DontUseNativeDialog` and centers dialogs on the correct monitor (fixes WSLg / multi-screen
mis-placement). Never call `QFileDialog`/`QMessageBox` directly. `app/config/icons.py` wraps
`qtawesome` (`mdi6.*` / `fa5s.*`) with graceful fallback when the package is absent — use
`icon()` / `set_button_icon()` everywhere instead of emoji. `app/config/effects.py` provides
`apply_card_shadow()` because QSS cannot express `box-shadow`; call it when a panel needs
elevation.

**Collab subsystem.** `app/services/collab_service.py` embeds FastAPI + uvicorn on port 5050
in `CollabServerThread` and auto-discovers peers via zeroconf mDNS in `CollabDiscoveryThread`.
A 5 s `QTimer` drives `CollabSyncWorker` for HTTP pulls. Conflict policy: creating a UID that
already exists on any online peer returns HTTP 409 — caller must abandon or rename. When mDNS
fails (VLANs, Windows Firewall), call `CollabService.add_manual_peer(ip, port)`. Offline edits
queue in `collab_offline_queue.py` and flush on reconnect.

**Export pipeline.** `app/services/export_service.py` produces Excel (34-column, oracle
`server.js:595-721`), CSV (UTF-8 BOM), and Darwin Core (reads the `darwin_core` SQLite view).
`app/services/organize_service.py` handles folder/file rename operations.
`app/services/retroactive_service.py` back-fills derived fields for records imported before
schema changes.

**Label subsystem.** Labels span five layers: `app/utils/label_core.py` (data/layout math,
no Qt — includes `plan_label_pages` for A4/A5 imposition), `app/utils/label_render.py` (QPainter
rendering onto QPixmap), `app/utils/label_print.py` (Qt `QPrinter` adapter — `build_printer` +
`paint_jobs`; multiple buckets print under one dialog, delegating pixels to `render_label_onto`
so on-screen preview and print stay WYSIWYG-identical), `app/services/label_service.py`
(persistence + template CRUD), and the widgets in `app/widgets/label_*.py` (step-flow dialog,
designer, editor, list/detail panels). The 4-step wizard is
`label_step1_select` → `label_step2_templates` → `label_step3_paper` → `label_step4_output`,
hosted by `LabelDesignerDialog` (`label_designer_dialog.py`). The `app/widgets/_collapse.py` and
`app/widgets/_form_row.py` helpers are reusable across all widgets for collapsible sections and
labeled form rows. `app/services/label_design_schema.py` is the Qt-free declarative schema
(element tools + bindable field options) shared by the template picker, designer, and renderer
tests — edit it there, not inline in widgets.

A template also carries an optional **free-form `elements` layer** (`normalize_elements` in
`label_core.py`): text / specimen-bound field / line / rect / ellipse / image (base64 inline) /
barcode (Code128), each freely positioned in mm (origin = label top-left), drawn by
`render_label_onto` *after* the rows (list order = z-order). Absent/empty `elements` renders
byte-identically to the old row-only path — this is gated by a test because the renderer also
drives the printer; never break it. `LabelDesignerDialog` is a full vector editor over these
elements: 8-handle resize + rotation handle, multi-select with align/distribute + copy/paste,
draggable rulers/guides and snap-to-grid/edge with red alignment guides, zoom/pan, a layers panel
with persistent groups, in-place text editing with a floating toolbar, per-element opacity / dash
/ font-family / gradient fill / drop-shadow, polygon shapes, a format brush + batch edit, A4/A5
imposition (margin/spacing/forced rows-cols/crop marks/multi-page preview), designer-editable
label dims (persisted as the `custom` size via `LabelTemplateLibrary.set_custom_dims`), a
margin/bleed overlay (designer-local, never printed), and a starter-preset gallery
(`app/services/label_presets.py`, kept out of `BUILTIN_TEMPLATES`). `python-barcode` is an OPTIONAL runtime dep — `_generate_barcode_pixmap`
soft-degrades to a placeholder box like `_generate_qr_pixmap` does for `qrcode`.

**Workbench right-rail cards.** The workbench right rail is composed of standalone card panels
(`app/widgets/taxon_card_panel.py`, `app/widgets/metadata_panel.py`, etc.), each porting one
web oracle card (`renderTaxonNotesCard`, `renderMetadataCard`). Cards own their own DB writes;
the workbench view assembles them into the rail layout. `TaxonCardPanel` opens
`taxon_edit_dialog.py` for bulk 5-level taxonomy editing — inline field edits remain available.

**Native tile map — no QtWebEngine.** Map point-picking and the coords-view interactive map run
on `app/widgets/tile_map_widget.py::TileMapWidget` (OSM raster tiles fetched async via
`QNetworkAccessManager`), which **replaced** the old `QWebEngineView` embed — `PyQt6-WebEngine` is
no longer a dependency. `map_pick_dialog.py` wraps `TileMapWidget`; its `available()` now always
returns `True`. The static `available() → bool` guard remains the convention for any heavy-optional
widget: callers check it before opening and fall back to manual input when `False`; never import or
instantiate such widgets unconditionally.

**CollabView** (`app/views/collab_view.py`) implements the collaboration module but is **not**
registered in `ALL_VIEWS` — it is not a nav tab. Collaboration surfaces inline in the workbench
sidebar panel, following the web oracle's workspace page layout. Pairing/diagnostics UI lives in
`collab_pairing.py` + `collab_diagnostics_dialog.py`.

**Project folder-tree (this branch's feature).** A "project" is a root folder; *any* subfolder at
*any* depth (断面/区域/样地/航次…) can itself be a photo workspace. `project_tree_service.py` does a
pure read-only scan (never creates dirs/DBs); `is_workspace(dir)` = the node already has
`_data/project.db`. `RESERVED_DIR_NAMES` (`_data`, `incoming-jpg`, `新拍JPG`, `results`,
`freeform`, `archive`) are workspace internals, not tree nodes. `ProjectTreeView` renders the tree
and lets the user enter any node as a workspace. Per-project settings (personnel, codeLabels,
tiffFields, customStorages, projectMeta — oracle app.js objects) persist in the `project_settings`
table via `project_settings_service.py`; child workspaces **inherit** parent settings (the
`folder-tree-inherit` branch). Editing UI is `project_settings_drawer.py`.

**采集记录 (collection records) — beyond the web oracle.** `collection_record_service.py` is CRUD
over the `collection_records` table, a field-collection log keyed by
`(province, site, station, collection_date)` — the same location segments the UID derives from
(`naming.py:42-60`). The workbench looks up a record by those keys and auto-fills the fields it
owns (collector/photographer/lon/lat/geo_area/dates); fields the capture UI has no slot for
(habitat/tide/…) live only here and rejoin at export. This is a NEW capability — the web oracle's
`code_labels.stations` is only `{code: label}`. `CollectionRecordsView` is the grid;
`collection_record_io.py` does Excel/CSV template export↔import (offline bulk fill);
`coord_import_service.py` bulk-imports station tables (Excel/CSV/TXT, any coord format → WGS84) via
`coord_import_dialog.py`.

**Geo / 采集地图 subsystem.** `CollectionMapView` visualizes station coordinates on a publication
basemap. Layers: `basemap_registry.py` discovers basemaps (user's `地图/` folder images + EPS,
bundled rasters in `resources/geo/basemaps/`, OSM tiles, procedurally-generated projections),
rasterizes EPS via Ghostscript (`gs`, degrades if absent), and persists per-image control-point
calibration as `<image>.calib.json` (reused across projects). `geo_calibration.py` fits a
lon/lat→pixel transform (order-1 affine ≥3 pts / order-2 polynomial ≥6 pts) from control points
clicked in `calibration_dialog.py`, reporting RMS residual; pure numpy. `geo_basemap.py` loads
bundled Natural Earth GeoJSON and projects via pyproj (optional dep — falls back to PlateCarree
identity). `geocode_service.py` is the single place-name geocoder (Nominatim default, biased
`countrycodes=cn`; 高德 AMap when a Web-服务 key is set, GCJ-02→WGS-84 converted) — its
`GeocodeWorker` signals MUST connect to a main-thread QObject slot (queued connection) or widget
updates corrupt. `publication_map_widget.py` + `marker_style_panel.py` render the styled map.

**Supplementary archival.** `supplementary_service.py` lets the desktop archive a JPG+TIFF bundle
WITHOUT an active specimen: it resolves the specimen from the TIFF filename (uniqueId, sequence
stripped) and validates the selection (≥1 JPG, exactly 1 TIFF, no unsupported) — ports
`app.js:3808-3824` + `4097-4123`. It only decides *what*/*which specimen*; actual cjxl/ZIP/safety
gates stay in `archive_service.py`. `supp_compression_worker.py` runs it off the UI thread.

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
