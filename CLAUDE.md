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

Before changing core workflow behavior, read `docs/PROJECT_MEMORY.md`. It records user-repeated
requirements that must not be rediscovered through chat.

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

python scripts/run_core_regression.py --list     # named regression suites for high-risk flows
python scripts/run_core_regression.py naming     # run one suite (quick/naming/workbench/...)
```

Windows (PowerShell): `scripts\run_tests_batched.ps1` runs the whole suite one pytest process
per file (sidesteps the full-run hang, see Conventions); `scripts\build_windows.ps1` builds the
portable PyInstaller package → `dist\SpecimenPhotoWorkbench-<ver>-win64.zip`. App version constant
lives in `app/config/version.py::APP_VERSION`.

External CLIs are detected at runtime, never bundled-by-default: `cjxl`/`djxl` (libjxl-tools /
brew jpeg-xl), Helicon Focus (detected only, never distributed). Their absence must degrade
gracefully, never crash.

Windows desktop double-click launch: `launch_windows.cmd` → `wsl.exe` into the project dir →
`python main.py`; on failure it keeps the error window + writes
`/tmp/specimen-photo-workbench-launch.log`.

`pytest.ini` pins `qt_api = pyqt6` on purpose: the dev box has PySide6 + PyQt6 co-installed, and
pytest-qt auto-loads PySide6 first — its older `libQt6Core` then gets reused by PyQt6 → missing
`Qt_6.11` symbols → whole-suite collection error. Do not delete that line. No linter/formatter is
configured; the **test suite is the only quality gate** (TDD red→green→commit per convention).

## Architecture

**DI + view registry shell.** `main.py` builds one `AppContext` (the single dependency-injection
container: settings + current project dir + DB access) and one `MainWindow`, then registers every
spec in `app/views/registry.py::ALL_VIEW_SPECS` (a tuple of `LazyViewSpec`). Views never import
each other — they reach shared state only through `ctx`.

- `app/app_context.py` — `AppContext`; `ctx.get_db()` returns the SQLite connection for the
  current project (or `None` if no project loaded). Also carries session-scoped flags reused as
  cross-view handoff state, e.g. `ctx.edit_unlocked` / `ctx.edit_actor` (data-filter edit lock).
- `app/views/registry.py` — `LazyViewSpec(view_id, nav_title, module, class_name)`: the real view
  module is imported lazily on first page open (`.resolve()`), not at startup. `register_view`
  accepts either a `LazyViewSpec` or a concrete `BaseView` subclass (legacy path). Stack index ==
  tuple order == web prototype topbar order.
- `app/main_window.py` — `QMainWindow` shell: top-bar segmented nav + context bar + bottom
  `QStatusBar`, with a `QStackedWidget` holding one page per view. **Nav pin system:** only specs
  in `_DEFAULT_PINNED_NAV` (or pinned via QSettings `ui/topbar_pinned_views`) show as top-bar
  buttons; the rest live in the 工具箱 overflow menu (`_nav_menu`, grouped by function). All nav
  labels go through `tr()` for i18n (see below).
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
the zero-field-loss fallback. `ensure_schema()` = executescript(schema.sql) →
`_migrate_add_missing_columns` (auto-ALTER: diffs the live DB against schema.sql materialized in
a throwaway in-memory DB — no hand-maintained column list) →
`app/db/migrations.py::run_pending_migrations` (numbered migrations tracked in the `_schema_meta`
version row; v2 = `specimen_result_tif_index`, see `app/db/result_tif_schema.py` +
`specimen_result_tif_service.py`, the index of master-TIF absolute paths used by summary /
photo-pick). Division of labor: new tables/columns go in schema.sql; data/index backfills go in
migrations.py. **Cross-workspace sweeps never use the cached connection**: anything that
touches *other* workspaces' dbs (KPI aggregation, result-tif index, summary enrich) opens
`open_project_db_private()` and closes it in `finally` — a cached conn per child holds its
file lock until exit (Windows: folder can't be moved/deleted; the historic shutdown-lock bug).
Read paths must not mutate child dbs (no `create=True`, no schema migration, no
`updated_at` rewrites) — `read_workspace_meta` is the pure-read API.

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

**Collab subsystem.** `app/services/collab_service.py` is the facade (`CollabService(QObject)`
owns all threads/timers/signals) over split flat modules it re-exports for back-compat:
`collab_types` (datatypes; shim → `app/services/collab/`), `collab_store` (`TaskStore` + LWW
merge), `collab_api` (FastAPI endpoints), `collab_net` (`CollabServerThread` — uvicorn, preferred
port 5050, scans forward for a free one — and `CollabDiscoveryThread` — zeroconf mDNS),
`collab_specimen_sync` (L2 specimen-row replication), `collab_file_sync` (L3 media
manifest/download), `collab_status` / `collab_project_bind` / `collab_share_registry` (which
local projects this machine advertises) / `collab_peer_trust` (trusted/blocked `ip:port` sets —
same-team peers are NOT synced until trusted). Sync is pull-based: a 5 s `QTimer` fires
`_sync_all_peers`, which only *schedules* — the cycle runs OFF the Qt main thread in a
short-lived daemon thread (`_spawn` → `_run_sync_cycle`), guarded by a non-blocking `_sync_lock`
(slow/dead peer ⇒ skipped firing, never a frozen UI); specimen sync piggybacks every 6th cycle.
Conflicts: creating a UID that exists on any online peer → HTTP 409, caller abandons or renames;
LWW merge has a clock-skew guard (`CLOCK_SKEW_THRESHOLD_MS` — a fast-clocked peer cannot silently
overwrite local status) plus best-effort claim-collision detection (two devices claiming one UID
within 30 s → warn only, P2P cannot prevent). Every data endpoint is gated by `_require_lan`
(non-private source IP → 403). When mDNS fails (VLANs, Windows Firewall):
`CollabService.add_manual_peer(ip, port)` or the periodic subnet scanner. Two distinct offline
queues, kept separate on purpose: `StatusRetryQueue` in `collab_offline_queue.py` (renamed from
`OfflineDraftQueue`, alias kept) retries failed *status* pushes; `CollabService._offline_drafts`
(`collab_drafts.json`) retries failed task *creation/claims*. `remote_collab_service.py` is a
separate WAN relay *client* stub (Bearer-token invites; no relay server in-repo — do not mix it
with LAN P2P); `app/api-gateway/` is an empty placeholder dir (README only, nothing executes).

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

**排版设计 (imposition designer).** A4/A5 拼版 freedom lives in three places:
`label_core.calculate_grid`/`plan_label_pages` accept opt-in `grid_opts` keys (per-side
`margin*Mm`, axis `gapX/YMm`, `forceCols/Rows` + `shrinkToFit` label scaling, `orientation`,
`startSlot` 残张续打) — **absent keys must stay byte-identical to the legacy formulas** (printer
geometry red line, gated by `TestCalculateGridLegacyParity` / `test_legacy_placements_byte_identical`);
`app/utils/label_sheet.py` is the shared sheet painter (true-mm cells + per-cell px rects) used by
the labels-view thumbnail, the 排版预览 dialog and `app/widgets/label_imposition_dialog.py`
(`LabelImpositionDialog` — live canvas with draggable margin/gap guides, click-to-set 起始格,
presets). Imposition persists per bucket via `label_service.persist_imposition` (whitelist
`sanitize_imposition`); jobs carry `job["gridOpts"]` so 一键双打 buckets print with their own
layout. Workbench quick print passes no grid_opts — intentionally legacy.

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

**CollabView** (`app/views/collab_view.py`) is a top-level nav tab (`view_id="collab"`, second
entry in `ALL_VIEW_SPECS`). The legacy workbench-sidebar collab drawer
(`workbench_collab_drawers.py`, `collab_panel.py`) still exists, but the tab is the primary
surface. Pairing = team permanent-code wizard (`collab_setup_wizard.py`); advertised-project
selection = `collab_share_project_picker.py` / `collab_aux_dialogs.py` (also the manual-IP
connect dialog); diagnostics in `collab_diagnostics_dialog.py`.

**Project folder-tree (this branch's feature).** A "project" is a root folder; *any* subfolder at
*any* depth (断面/区域/样地/航次…) can itself be a photo workspace. `project_tree_service.py` does a
pure read-only scan (never creates dirs/DBs); `is_workspace(dir)` = the node already has
`_data/project.db`. `RESERVED_DIR_NAMES` (`_data`, `incoming-jpg`, `新拍JPG`, `results`,
`freeform`, `archive`) are workspace internals, not tree nodes. `ProjectTreeView` renders the tree
and lets the user enter any node as a workspace. Per-project settings (personnel, codeLabels,
tiffFields, customStorages, projectMeta — oracle app.js objects) persist in the `project_settings`
table via `project_settings_service.py`; child workspaces **inherit** parent settings (the
`folder-tree-inherit` branch). Editing UI is `project_settings_drawer.py`. Around the tree:
`project_catalog_service.py` (the survey ROOT's `_data/project.db` catalogs which child dirs are
managed workspaces), `project_adopt_service.py` (adopt an existing folder with **zero migration**
— only creates `_data/project.db` + registers; the first 进入 runs full workspace init), and
`workspace_index_service.py` (per-workspace stats cached in the root db's
`workspace_index_cache`; refresh is explicit, reads fall back to a live scan).

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

**Cross-断面 survey hub (three-column ProjectTreeView).** `ProjectTreeView` implements
`docs/specs/2026-07-08-survey-summary-view.md`. Left = multi-select folder tree (Ctrl/Shift
断面 multi-select; double-click enters a workspace unless ≥2 nodes are selected) plus a project
card view (`project_card.py`, cover image via `cover_pick_service`). Center = 数据汇总 panel:
inline `SpecimenFilterPanel`, a column-pickable / Excel-style-filterable 编号表
(`summary_column_picker_dialog.py`, `summary_column_filter_dialog.py`; in-memory sort/filter in
`app/utils/summary_table_ops.py`) and a UID-grouped virtualized photo grid
(`uid_grouped_grid.py` + `project_tree_uid_index.py` jump rail). Right = `SurveyOverviewPanel`
(KPI numbers + OSM mini-map + species list). Shared query entry:
`cross_workspace_query_service.py::query_summary_scope` (metadata filter → photo association →
stats; also owns the summary-column API). Aggregators: `survey_overview_service.py` (shim →
`services/project/`) and `taxon_inventory_service.py` (species inventory, dedup by
`scientific_name`) — all read-only, tolerant of missing/corrupt/locked workspace dbs (skip,
never throw). Toolbar: 汇总导出…, 站位物种, and 数据筛选… (opens the dialog below).

**数据筛选 (data filter) — beyond the web oracle.** `DataFilterView` is a **modal dialog**, not a
nav page (demoted from `ALL_VIEW_SPECS`; opened via
`app/widgets/data_filter_dialog.py::open_data_filter_dialog` from the project-tree toolbar, with
the tree-selected workspaces preselected). Cross-workspace field filtering ("哪些编号取了 RNA",
"某拍摄人拍了多少", "某地区有多少标本"). Four pieces, all dynamic — **no field list is
hardcoded** so schema upgrades need no code change:
- `app/config/specimen_fields.py` — filterable fields = `PRAGMA table_info(specimens)` ∪
  `FIELD_META` (中文 labels only) ∪ `DERIVED` (computed dimensions like `storage_is_rna` =
  R-prefix match per oracle `app.js:300`). `filterable_fields(db)` introspects at runtime.
- `app/services/specimen_filter_service.py` — `query_specimens(workspaces, conditions, labels)`:
  read-only SELECT across each workspace's `_data/project.db`, in-memory merge, AND-filter
  (derived dims post-filter). **Pure SELECT, never writes** (red-line style).
- `app/services/edit_lock_service.py` — edit gate for this page only. Password (sha256, default
  `"123"`, stored in `data/app_config.json`, plaintext never persisted) + mandatory modifier name.
  `unlock(ctx, actor, plain)` sets `ctx.edit_unlocked=True` + `ctx.edit_actor=actor` for the
  session; `lock(ctx)` resets. Edits call `require_unlock(ctx)`. **Default mode is read-only preview.**
- Result row = specimen list + photo thumbnail (`_find_specimen_photo` reads `<uid>*.tif` from the
  workspace's `results`/`freeform`) + per-field statistics. Edits audit-log the actor.

**项目汇总 (SummaryView)** (`view_id="summary"`) — cross-project specimen summary table as a nav
page: project-filter dropdown, collapsible field-selection panel (26 default / 34 total columns),
Excel / CSV / Darwin Core export. Backed by `project_summary_service.py` (merges specimens +
collection_records + grouping across workspace DBs, zero new tables) and `export_service`.
Distinct from the project-tree center 数据汇总 panel; the overlap is known and intentional.

**TIFF 转 JPG tool** (`view_id="tiff_jpeg_tool"`) — batch TIFF→JPEG export page.
`tiff_jpeg_export_service.py`: presets smart/web/general/archive/max, `recommend_export_settings`
(smart mode picks quality/resize/subsampling from dims/bit-depth), ThreadPoolExecutor ≤4, PIL
decode with QImage-thumbnail fallback; QThread worker in `app/workers/`. **Masters are
read-only** — the service asserts source stat unchanged after each write. Other pages hand off
via `ctx.pending_tiff_jpeg_sources` / `ctx.pending_tiff_jpeg_preset_id`.

**Preview/perf layer.** `app/utils/thumbnail_disk_cache.py` — persistent JPEG thumbnail cache
under `data/cache/thumbnails/`, key = sha256(resolved path, mtime_ns, size, size-bucket) so file
edits auto-invalidate; a TIFF is decoded ONCE to a 720 px master JPEG
(`TIFF_PREVIEW_MASTER_SIZE`), grid-density changes only rescale it.
`tiff_preview_warmup_service.py` + its worker pre-warm those masters in the background after a
数据汇总 load. `app/config/memory_profile.py` sets in-memory cache ceilings once at startup
(halved in low-RAM/performance mode); `app/config/preview_profile.py` holds preview master size +
JPEG quality synced from AppSettings. `app/utils/tiff_exif_read.py` reads camera EXIF from the
master TIFF (Pillow IFD → tifffile → optional `exiftool` CLI → embedded preview-JPEG strip).

**Auto-update.** `app/services/update_service.py` + `app/widgets/update_controller.py`. Feed =
GitHub Releases of `deyuanyang92-dev/photo-manager-ydy` (win64 zip asset); integrity = size +
GitHub-provided sha256; authenticity = optional Ed25519 detached `.sig` (embedded
`UPDATE_PUBLIC_KEY_B64`, env override `SPECIMEN_UPDATE_PUBKEY`; once a key is configured, an
unsigned release is refused). Self-update only runs as the frozen Windows exe; apply = generated
PowerShell script (backup → swap → offscreen `--smoke` test → rollback on failure) that preserves
`PROTECTED_RELATIVE_PATHS` user data; zip extraction is hardened (path-traversal/symlink/entry
caps). `UpdateController` does the silent 6-hourly background check. Keypair/signing tooling:
`scripts/gen_update_keys.py`, `scripts/sign_release.py`. NB: `activation_service.py` is NOT
licensing — it is *specimen* activation (one active specimen at a time + append-only event log in
`_data/state.json`, an oracle port).

**i18n (CN/EN).** `app/config/i18n.py` provides `tr(中文源)` → looks up `resources/en.json`; source
strings are Chinese, English is the translation. All UI text goes through `tr()`; language switch
takes effect on restart. Wrap new user-facing strings in `tr()`.

## Hard red lines (never violate — these are the reason the project exists)

1. **TIFF is never *auto*-deleted.** Archive / organize / compose / any background flow must
   never delete a TIFF (it is the lossless master). **Manual deletion IS allowed** — the project
   owner overrode the old absolute "never delete" rule: a TIFF can be deleted through a deliberate,
   user-initiated action behind a confirmation dialog (monitor card right-click → 删除此文件, with
   an irreversible-loss confirm). Do not re-block manual TIFF deletion; do not parrot "TIFF 永不删".
2. **JPG deletion requires all four preconditions** (`app/services/archive_service.py`, oracle
   `archive.js:28-61`): cjxl available + ZIP exists (>32 B) + `verify_manifest_complete` +
   `verify_jxl_recoverable` (djxl actually re-decodes each JXL). **Default delete_jpg=False.**
   If djxl is missing, check (d) fails → JPGs are NOT deleted.
3. **Selected-JPG compose/organise does not require active UID.** If the monitor has selected JPGs,
   `合成` and `合成+整理` must process that explicit selection without showing "请先激活编号".
   Active UID exists → auto-name under the active UID. No active UID → prompt for target UID or
   free output stem; target UID assigns/moves JPGs and auto-names, free stem names both TIF and
   ZIP. Existing JPG attribution is only a hint/default when no UID is active. Full rule:
   `docs/specs/photo-grouping-workflow.md`.
4. **Import is strictly read-only** (`app/services/import_service.py`): source `data/*.json` is
   sha256-snapshotted before and re-verified after; any change raises `IntegrityError`. Corrupt
   JSON aborts with no partial writes. Per-row `INSERT OR REPLACE` (idempotent), not
   "skip if table non-empty".
5. **cjxl flags are exactly `--distance 0 -e <effort>`** (lossless bit-exact). Never
   `--quality`/`--modular`/`-j` (oracle `compress.js:32-39`).
6. Path safety = stateful `SafePathRegistry`, `..` checked via `relative_to` (oracle
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
  **Run a single test file** (`pytest tests/<file> -v`), not the whole suite — full runs hit a
  WorkbenchView self-loop QTimer leak / collab SegFault that hangs the run; the per-file path is
  stable. `conftest.py` has an autouse fixture isolating `data/user_projects.json` to tmp to stop
  cross-test pollution.
- Module views: subclass `BaseView`, add a `LazyViewSpec` to `ALL_VIEW_SPECS` (NOT `ALL_VIEWS`,
  which is the legacy concrete-class compatibility shim); `MainWindow` wires nav + stack.
- UI text is Chinese-first, wrapped in `tr()`. Commits follow Conventional Commits
  (`feat(scope): ...`), Chinese subjects are the norm here.
- **Keep old code commented, don't delete** (project rule, "§7" in commit comments): when replacing
  existing logic, comment the old lines out with `#` and write the new implementation beside them.
  Old code stays until explicitly told to remove. This applies to ported oracle logic in particular
  — the commented original is the cross-reference to the JS file:line.
- File placement follows `docs/architecture/directory-boundaries.md` (+ per-dir READMEs in
  `app/services|views|widgets`). `app/services/` subdomain dirs (`project/`, `specimen/`,
  `taxonomy/`, `label/`, `collab/`) are incremental-migration anchors: new services prefer a
  subdomain; a moved module leaves a same-name compat shim (`sys.modules` alias — e.g.
  `cover_pick_service.py`, `survey_overview_service.py`) so old import paths keep working — never
  rewrite imports repo-wide. View vs widget: registrable in `registry.py` ⇒ view; assembled by
  multiple views ⇒ widget.
- `docs/adr/` = accepted decisions; `docs/specs/` = per-module implementation specs;
  `docs/shots/` = web-vs-Qt comparison screenshots (capture scripts alongside).

## UI design freeze

Existing UI layout / visual style / confirmed UX flows must not be changed without an explicit
user instruction in the current conversation. Fix functional bugs without altering appearance
unless the user asks otherwise. (Mirrors the global rule in `~/.claude/CLAUDE.md`.)
