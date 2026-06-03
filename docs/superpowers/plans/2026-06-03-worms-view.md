# WoRMS View — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the web prototype's WoRMS 分类库 page to a complete PyQt6 desktop view with full feature parity: fuzzy/exact search, result list (accepted/synonym badges), classification chain, overview/children/synonyms tabs, fill-to-specimen, batch validation jobs, and disk cache.

**Architecture:** `WormsView(BaseView)` is the page widget; `_DetailPanel` handles right-side detail; two `QObject` workers (`_SearchWorker`, `_DetailWorker`) run on `QThread` for all network I/O. `WormsService` is a standalone Python service that proxies marinespecies.org REST API with JSON disk cache and rate limiter.

**Tech Stack:** PyQt6, httpx (sync), threading.Lock, JSON file persistence, pytest (offscreen QT_QPA_PLATFORM)

---

## Status

**COMPLETE.** All tasks below have been executed and committed.

- `app/views/worms_view.py` — 1134 lines, full implementation
- `app/services/worms_service.py` — 620 lines, full implementation
- `tests/test_worms_service.py` — 33 tests, all pass
- `docs/specs/worms-functional.md` — functional spec with web oracle line refs
- `docs/shots/worms_func.png` — 1920×1080 screenshot with synthetic data

Commit: `654b7d8` docs(worms): add functional spec and worms_func.png screenshot

---

## File Structure

| File | Role |
|------|------|
| `app/views/worms_view.py` | WormsView page + _DetailPanel + workers |
| `app/services/worms_service.py` | WoRMS proxy, cache, rate limiter, batch jobs |
| `tests/test_worms_service.py` | pytest: service + offscreen view smoke tests |
| `docs/specs/worms-functional.md` | Functional spec with web oracle line numbers |
| `docs/shots/worms_func.png` | Themed 1920×1080 screenshot |
| `docs/shots/capture_worms.py` | Capture script for the screenshot |

---

## Task 1: WormsService — cache, rate limiter, search/classification/synonyms/children/record

**Files:**
- `app/services/worms_service.py`
- `tests/test_worms_service.py`

- [x] **Step 1: Write failing tests for WormsService cache hit**

```python
def test_search_returns_cached_result(self, tmp_path):
    svc = _make_service(str(tmp_path))
    _write_cache(svc._cache_path, {"search:A:like": {"data": [{"AphiaID": 1}], "fetched_at": _iso_ago(60)}})
    with patch("httpx.get") as mock_get:
        result = svc.search("A", like=True)
        mock_get.assert_not_called()
    assert result == [{"AphiaID": 1}]
```

- [x] **Step 2: Run to verify failure** — `pytest tests/test_worms_service.py::TestCacheHit -v` FAIL (no WormsService)

- [x] **Step 3: Implement WormsService._fetch with cache** — `worms_service.py:228–288`

- [x] **Step 4: Implement search/classification/synonyms/children/record** — `worms_service.py:292–376`

- [x] **Step 5: Run tests** — PASS

- [x] **Step 6: Commit** — included in main commit

---

## Task 2: WormsService — Chinese-field protection (merge_worms_into_record)

- [x] **Step 1: Write failing test**

```python
def test_cn_fields_are_preserved_unchanged(self):
    original = {"familyCn": "刺尾鱼科"}
    merged = WormsService.merge_worms_into_record(dict(original), {"AphiaID": 1, ...})
    assert merged["familyCn"] == "刺尾鱼科"
```

- [x] **Step 2:** Run FAIL
- [x] **Step 3:** Implement `merge_worms_into_record` — `worms_service.py:411–476`
- [x] **Step 4:** Run PASS
- [x] **Step 5:** Commit

---

## Task 3: WormsService — batch jobs (create/list/get/update)

- [x] **Step 1: Write failing tests**

```python
def test_create_job_persists(self, tmp_path):
    svc = _make_service(str(tmp_path))
    job = svc.create_job(["r001", "r002"])
    assert job.status == "running"
    assert svc.get_job(job.id) is not None
```

- [x] **Step 2:** Run FAIL
- [x] **Step 3:** Implement `create_job`, `list_jobs`, `get_job`, `update_job_status` — `worms_service.py:481–568`
- [x] **Step 4:** Run PASS
- [x] **Step 5:** Commit

---

## Task 4: WormsService — cache eviction and clear_expired

- [x] **Step 1: Write failing test**

```python
def test_clear_expired_removes_stale_entries(self, tmp_path):
    svc = _make_service(str(tmp_path))
    _write_cache(svc._cache_path, {
        "search:OldName:like": {"data": [], "fetched_at": _iso_ago(TTL_SEARCH + 3600)},
        "search:NewName:like": {"data": [{"AphiaID": 1}], "fetched_at": _iso_ago(60)},
    })
    removed = svc.clear_expired()
    assert removed == 1
```

- [x] **Step 2:** Run FAIL
- [x] **Step 3:** Implement `clear_expired`, `_evict_if_needed` — `worms_service.py:205–217`, `585–619`
- [x] **Step 4:** Run PASS
- [x] **Step 5:** Commit

---

## Task 5: WormsView — full UI (search panel + detail panel + tabs + fill + batch jobs)

**Files:**
- `app/views/worms_view.py`

- [x] **Step 1: Write offscreen smoke test**

```python
def test_worms_view_constructs(self, qt_app, tmp_path):
    from app.views.worms_view import WormsView
    ctx = MagicMock()
    ctx.current_project_dir = str(tmp_path)
    view = WormsView(ctx)
    assert view.view_id == "worms"
    assert view.nav_title == "WoRMS 分类库"
```

- [x] **Step 2:** Run FAIL (no WormsView)
- [x] **Step 3:** Implement WormsView with all UI zones — `worms_view.py:633–1134`
- [x] **Step 4:** Run PASS
- [x] **Step 5:** Commit

---

## Task 6: Functional spec + screenshot

**Files:**
- `docs/specs/worms-functional.md`
- `docs/shots/worms_func.png`

- [x] **Step 1: Write spec with oracle line numbers** — `docs/specs/worms-functional.md`

- [x] **Step 2: Generate screenshot**

```bash
QT_QPA_PLATFORM=offscreen python3 docs/shots/capture_worms.py
cp docs/shots/page_worms.png docs/shots/worms_func.png
```

Expected: `docs/shots/worms_func.png` 1920×1080, >5000 bytes

- [x] **Step 3: Verify all 33 tests pass**

```bash
pytest tests/test_worms_service.py -v
# 33 passed
```

- [x] **Step 4: Commit**

```bash
git add docs/specs/worms-functional.md docs/shots/worms_func.png
git commit -m "docs(worms): add functional spec and worms_func.png screenshot"
```

---

## Self-Review Checklist

- [x] Spec coverage: all 10 features from task description have spec sections
- [x] Placeholder scan: no TBD/TODO
- [x] Type consistency: `_SearchWorker.finished → list`, `_DetailWorker.finished → dict`, matches usage in `_on_search_done(results: list[dict])` and `_on_detail_done(data: dict)`
- [x] Chinese-field protection: red line covered by 4 dedicated tests
- [x] fill-to-specimen: documented as hook pattern (`ctx.worms_fill_specimen`)
