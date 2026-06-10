"""geocode_service.py — unified place-name geocoding (Nominatim + AMap).

Single source of truth for the 坐标工具「搜索地名」box *and* the map widget's
in-map search.  Replaces the two divergent inline workers (`_Geocoder` in
``coords_view`` and ``_NominatimWorker`` in ``tile_map_widget``).

Two backends, selected by whether an AMap **Web 服务** key is configured:

* ``nominatim`` (default, no key) — OpenStreetMap search, biased to China via
  ``countrycodes=cn`` so「北海」resolves to 北海市, not the European North Sea.
* ``amap`` (when a key is present) — 高德 REST ``place/text``; mirrors the web
  oracle's ``AMap.PlaceSearch`` (app.js:13393).  AMap returns GCJ-02 coords,
  converted back to WGS-84 via :func:`coord_utils.gcj02_to_wgs84`.

All results share one shape::

    {"name": str, "wgs": {"lat": float, "lon": float}}

``GeocodeWorker`` is a thin ``QObject`` wrapper; callers MUST connect its
``done``/``failed`` signals to a slot that lives on the **main thread** (a bound
method of a QObject with main-thread affinity) so Qt uses a queued connection —
connecting to a bare local closure makes Qt run the slot on the worker thread,
which corrupts non-thread-safe widget updates (the original「搜索中...」hang).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from app.utils.coord_utils import gcj02_to_wgs84, nominatim_to_zh

# Unified User-Agent (was inconsistent between the two old workers).
_UA = "photo-platform-gui/1.0 (specimen-workbench)"
_TIMEOUT = 6
_LIMIT = 5


def _opener_for(url: str):
    """OSM 域名且探测到本地代理时返回带代理的 opener；其余返回 None（直连）。

    Nominatim 直连在大陆被拦（TCP 挂死）；net_proxy 自动发现 Clash 类本地代理。
    ``detect_osm_proxy`` 是阻塞缓存式探测 —— 本函数只会在 GeocodeWorker 的
    工作线程里被调用，不会卡 UI。AMap 国内直连可达，永不走代理。
    """
    host = urllib.parse.urlsplit(url).hostname or ""
    if not host.endswith("openstreetmap.org"):
        return None
    from app.utils import net_proxy
    proxy = net_proxy.detect_osm_proxy()
    if not proxy:
        return None
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    )


def _http_get_json(url: str, *, headers: Optional[dict] = None, timeout: int = _TIMEOUT):
    """GET ``url`` and parse JSON.  Single network choke-point (tests patch this)."""
    req = urllib.request.Request(url, headers=headers or {"User-Agent": _UA})
    opener = _opener_for(url)
    open_fn = opener.open if opener is not None else urllib.request.urlopen
    with open_fn(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _search_nominatim(query: str, *, timeout: int = _TIMEOUT) -> list[dict]:
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "limit": _LIMIT,
        "accept-language": "zh",
        "countrycodes": "cn",
        "addressdetails": 1,  # carry 省/市 so same-named places (两个「三门湾」) stay distinct
    })
    data = _http_get_json(
        url,
        headers={"User-Agent": _UA, "Accept": "application/json"},
        timeout=timeout,
    )
    results: list[dict] = []
    for item in data or []:
        try:
            lat = float(item["lat"])
            lon = float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        name = nominatim_to_zh(item) or (item.get("display_name", "?") or "?")[:60]
        results.append({"name": name[:80], "wgs": {"lat": lat, "lon": lon}})
    return results


def _search_amap(query: str, amap_key: str, *, timeout: int = _TIMEOUT) -> list[dict]:
    url = "https://restapi.amap.com/v3/place/text?" + urllib.parse.urlencode({
        "key": amap_key,
        "keywords": query,
        "offset": _LIMIT,
        "page": 1,
    })
    data = _http_get_json(url, timeout=timeout)
    results: list[dict] = []
    for poi in (data or {}).get("pois", []) or []:
        loc = poi.get("location") or ""
        try:
            gcj_lon, gcj_lat = (float(x) for x in loc.split(","))
        except (ValueError, AttributeError):
            continue
        wgs = gcj02_to_wgs84(gcj_lon, gcj_lat)
        region = "·".join(
            dict.fromkeys(  # de-dup preserving order (pname may == cityname)
                p for p in (poi.get("pname"), poi.get("cityname"), poi.get("adname")) if p
            )
        )
        poi_name = poi.get("name") or "?"
        name = f"{poi_name}（{region}）" if region else poi_name
        results.append({"name": name[:80], "wgs": {"lat": wgs["lat"], "lon": wgs["lon"]}})
    return results


def geocode(
    query: str,
    *,
    backend: str = "nominatim",
    amap_key: str = "",
    timeout: int = _TIMEOUT,
) -> list[dict]:
    """Geocode ``query`` via the selected backend.  Returns ``[]`` on no match.

    Raises on network/parse failure — callers (or :class:`GeocodeWorker`) decide
    whether to surface or swallow.
    """
    query = (query or "").strip()
    if not query:
        return []
    if backend == "amap" and amap_key.strip():
        return _search_amap(query, amap_key.strip(), timeout=timeout)
    return _search_nominatim(query, timeout=timeout)


def resolve_backend(settings) -> tuple[str, str]:
    """Pick ``(backend, amap_key)`` from :class:`AppSettings`.  Key present → AMap."""
    key = ""
    try:
        key = (settings.amap_web_key or "").strip()
    except AttributeError:
        key = ""
    return ("amap", key) if key else ("nominatim", "")


class GeocodeWorker(QObject):
    """Runs :func:`geocode` off-thread.  ``done(list)`` / ``failed(str)``."""

    done = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, query: str, *, backend: str = "nominatim", amap_key: str = "") -> None:
        super().__init__()
        self._query = query
        self._backend = backend
        self._amap_key = amap_key

    def run(self) -> None:
        try:
            results = geocode(self._query, backend=self._backend, amap_key=self._amap_key)
        except Exception as exc:  # noqa: BLE001 — surface, never crash the thread
            self.failed.emit(str(exc))
            return
        self.done.emit(results)
