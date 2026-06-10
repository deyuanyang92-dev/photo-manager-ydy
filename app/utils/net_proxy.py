"""net_proxy.py — OSM-scoped HTTP proxy auto-detection.

openstreetmap.org（瓦片 + Nominatim）在大陆网络环境直连不通：TCP SYN 被丢弃，
请求挂死到超时。用户桌面上通常跑着 Clash 类代理（Windows 宿主或本机），但从
桌面启动的 GUI 进程没有 http_proxy 环境变量，Qt/urllib 都不会走代理 → 地图
永远空白。本模块自动发现可用的本地代理，**只**给 OSM 相关请求使用：

- 绝不设置全局 ``QNetworkProxy.setApplicationProxy`` / ``os.environ`` —— 那会把
  局域网协作同步（collab_service 5050 端口互拉）也劫持进代理，直接打断同步。
- 探测顺序：环境变量 → 直连测试（通则无需代理）→ 候选主机×Clash 常用端口。
- 候选主机：127.0.0.1、默认网关（WSL NAT 模式下 = Windows 宿主）、resolv.conf
  nameserver（WSL 旧版 = 宿主）。
- 结果进程内缓存；``start_detection()`` 在后台 daemon 线程跑一次，UI 永不阻塞。

纯标准库，无 Qt 依赖（QNetworkProxy 的应用在 TileMapWidget 侧做）。
"""
from __future__ import annotations

import os
import socket
import struct
import threading
import urllib.request
from typing import Optional

# Clash/Clash Verge 常用本地端口（用户实测 7892；7890 mixed 默认；7897 Verge 默认）
_PROBE_PORTS: tuple = (7892, 7890, 7897)
# 探测用最小瓦片（z2 ≈ 10 KB）；直连/经代理各试一次
_TEST_URL = "https://tile.openstreetmap.org/2/3/1.png"
_UA = "photo-platform-gui/1.0 (specimen-workbench)"

_lock = threading.Lock()
_detected = False
_result: Optional[str] = None
_thread: Optional[threading.Thread] = None


def proxy_from_env() -> Optional[str]:
    """环境变量里的 http(s) 代理；socks 代理 urllib 不支持，忽略。"""
    for var in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY"):
        val = (os.environ.get(var) or "").strip()
        if val.startswith("http://") or val.startswith("https://"):
            return val
    return None


def _read_quiet(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _gateway_from_proc(text: str) -> Optional[str]:
    """默认网关，解析 /proc/net/route（Gateway 为小端 hex）。"""
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "00000000":
            try:
                return socket.inet_ntoa(struct.pack("<L", int(parts[2], 16)))
            except (ValueError, struct.error):
                continue
    return None


def _nameserver_from_resolv(text: str) -> Optional[str]:
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "nameserver":
            return parts[1]
    return None


def candidate_hosts() -> list[str]:
    """本机优先，其次 WSL 宿主候选（默认网关 / DNS）。去重保序。"""
    hosts = ["127.0.0.1"]
    gw = _gateway_from_proc(_read_quiet("/proc/net/route"))
    if gw:
        hosts.append(gw)
    ns = _nameserver_from_resolv(_read_quiet("/etc/resolv.conf"))
    if ns:
        hosts.append(ns)
    return list(dict.fromkeys(hosts))


def _port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _fetch_ok(url: str, proxy: Optional[str], timeout: float = 5) -> bool:
    """GET ``url``（经 ``proxy``，None=直连）是否 HTTP 200。"""
    try:
        if proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            )
        else:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with opener.open(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001 — 探测失败一律视为不可用
        return False


def detect_osm_proxy(force: bool = False) -> Optional[str]:
    """阻塞式探测（结果缓存）。返回代理 URL，或 None（直连可用/未找到）。

    只应在工作线程调用（GeocodeWorker / start_detection 的 daemon 线程）。
    """
    global _detected, _result
    with _lock:
        if _detected and not force:
            return _result
        env = proxy_from_env()
        if env:
            _result = env
        elif _fetch_ok(_TEST_URL, None, timeout=4):
            _result = None          # 直连通，无需代理
        else:
            _result = None
            for host in candidate_hosts():
                for port in _PROBE_PORTS:
                    if not _port_open(host, port):
                        continue
                    cand = f"http://{host}:{port}"
                    if _fetch_ok(_TEST_URL, cand, timeout=5):
                        _result = cand
                        break
                if _result:
                    break
        _detected = True
        return _result


def osm_proxy() -> Optional[str]:
    """非阻塞：已探测则返回结果，未探测返回 None（不触发网络）。"""
    with _lock:
        return _result if _detected else None


def start_detection() -> None:
    """后台 daemon 线程跑一次探测；幂等，可在任意线程调用。"""
    global _thread
    with _lock:
        if _detected or (_thread is not None and _thread.is_alive()):
            return
        _thread = threading.Thread(
            target=detect_osm_proxy, name="osm-proxy-detect", daemon=True
        )
        _thread.start()


def _reset_for_tests() -> None:
    global _detected, _result, _thread
    with _lock:
        _detected = False
        _result = None
        _thread = None
