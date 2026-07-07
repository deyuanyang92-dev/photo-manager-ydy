"""collab_net.py — Background network threads for collaboration.

CollabServerThread    — FastAPI + uvicorn embedded server (QThread)
CollabDiscoveryThread — zeroconf mDNS registration + peer discovery (QThread)
"""
from __future__ import annotations

import asyncio
import logging
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from PyQt6.QtCore import QThread, pyqtSignal

from app.models.activity_log import ActivityLog
from app.services.collab_api import _build_fastapi_app
from app.services.collab_store import TaskStore
from app.services.collab_types import _get_local_ip

logger = logging.getLogger(__name__)

_MDNS_SERVICE_TYPE = "_specimen._tcp.local."


class CollabServerThread(QThread):
    """Runs FastAPI + uvicorn in a background QThread.

    Signals
    -------
    started_on_port(int):   emitted when server is listening.
    server_error(str):      emitted if startup fails.
    """

    started_on_port = pyqtSignal(int)
    server_error = pyqtSignal(str)

    def __init__(self, store: TaskStore, node_info_fn: Callable[[], dict],
                 preferred_port: int = 5050,
                 activity_log: Optional[ActivityLog] = None,
                 file_manifest_fn: Optional[Callable[[Optional[list[str]]], dict]] = None,
                 file_path_fn: Optional[Callable[[str], Path]] = None,
                 pairing_request_fn: Optional[Callable[[str, str, str], None]] = None,
                 pairing_accept_fn: Optional[Callable[[str, str], None]] = None,
                 specimen_provider_fn: Optional[Callable[[Optional[str]], list]] = None,
                 specimen_writer_fn: Optional[Callable[[list], int]] = None,
                 photo_index_fn: Optional[Callable[[str, str, int, str], None]] = None) -> None:
        super().__init__()
        self._store = store
        self._node_info_fn = node_info_fn
        self._preferred_port = preferred_port
        self._activity_log = activity_log
        self._file_manifest_fn = file_manifest_fn
        self._file_path_fn = file_path_fn
        self._pairing_request_fn = pairing_request_fn
        self._pairing_accept_fn = pairing_accept_fn
        self._specimen_provider_fn = specimen_provider_fn
        self._specimen_writer_fn = specimen_writer_fn
        self._photo_index_fn = photo_index_fn
        self._actual_port: Optional[int] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server: Optional[Any] = None  # uvicorn.Server, set in run()

    @property
    def actual_port(self) -> Optional[int]:
        return self._actual_port

    def _find_free_port(self, start: int) -> int:
        port = start
        while port < start + 20:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", port)) != 0:
                    return port
            port += 1
        raise OSError("No free port found near %d" % start)

    def run(self) -> None:
        try:
            import uvicorn
        except ImportError:
            self.server_error.emit("uvicorn not installed")
            return

        try:
            port = self._find_free_port(self._preferred_port)
        except OSError as exc:
            self.server_error.emit(str(exc))
            return

        self._actual_port = port
        app = _build_fastapi_app(
            self._store,
            self._node_info_fn,
            self._activity_log,
            file_manifest_fn=self._file_manifest_fn,
            file_path_fn=self._file_path_fn,
            pairing_request_fn=self._pairing_request_fn,
            pairing_accept_fn=self._pairing_accept_fn,
            specimen_provider_fn=self._specimen_provider_fn,
            specimen_writer_fn=self._specimen_writer_fn,
            photo_index_fn=self._photo_index_fn,
        )

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=port,
            loop="asyncio",
            log_level="warning",
        )
        server = uvicorn.Server(config)
        self._server = server

        # Emit port once server startup is complete (uvicorn calls startup first)
        async def _serve() -> None:
            await server.serve()

        async def _runner() -> None:
            # Small delay then emit so callers know the port
            serve_task = self._loop.create_task(_serve())  # type: ignore[union-attr]
            await asyncio.sleep(0.3)
            self.started_on_port.emit(port)
            await serve_task

        try:
            self._loop.run_until_complete(_runner())
        except Exception as exc:  # noqa: BLE001
            self.server_error.emit(str(exc))
        finally:
            self._loop.close()

    def stop(self) -> None:
        # Ask uvicorn to shut down gracefully (closes its listening sockets +
        # in-flight handlers via should_exit). Force-stopping the asyncio loop
        # instead strands the serve() coroutine and can leave the socket in
        # CLOSE_WAIT — and on Windows that can keep this QThread alive long
        # enough to hold the SQLite DB handle past exit. Fallback to a hard
        # loop.stop() only if the server never started.
        srv = getattr(self, "_server", None)
        loop = self._loop
        if srv is not None and loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(setattr, srv, "should_exit", True)
            except RuntimeError:  # loop closed between the check and the call
                pass
        elif loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        self.quit()
        self.wait(5000)
        # Last-resort hard stop: if uvicorn ignored should_exit for 5+ s (stuck
        # handler / CLOSE_WAIT socket on Windows), terminate so this QThread
        # cannot keep the python process alive past app.exit() — which would
        # re-introduce the must-reboot lock leak. The DB itself is not held by
        # this thread, so terminate() here only risks an orphaned uvicorn task.
        if self.isRunning():
            self.terminate()
            self.wait(1000)


# ── mDNS discovery thread ─────────────────────────────────────────────────────

class CollabDiscoveryThread(QThread):
    """Registers this node's mDNS service and discovers peers.

    Signals
    -------
    peer_found(str, int, str):    ip, port, hostname
    peer_lost(str, int):          ip, port
    """

    peer_found = pyqtSignal(str, int, str)    # ip, port, hostname
    peer_lost  = pyqtSignal(str, int)         # ip, port
    discovery_error = pyqtSignal(str)         # mDNS unavailable / register failed

    def __init__(self, hostname: str, port: int) -> None:
        super().__init__()
        self._hostname = hostname
        self._port = port
        self._zc: Any = None
        self._info: Any = None
        self._browser: Any = None

    def run(self) -> None:
        try:
            from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf
            import ipaddress
        except ImportError:
            logger.warning("zeroconf not installed — mDNS discovery disabled")
            self.discovery_error.emit("未安装 zeroconf")
            return

        local_ip = _get_local_ip()
        name = f"{self._hostname}.{_MDNS_SERVICE_TYPE}"

        try:
            addr_bytes = socket.inet_aton(local_ip)
        except OSError:
            addr_bytes = socket.inet_aton("127.0.0.1")

        self._info = ServiceInfo(
            _MDNS_SERVICE_TYPE,
            name,
            addresses=[addr_bytes],
            port=self._port,
            properties={"hostname": self._hostname.encode()},
        )

        self._zc = Zeroconf()

        try:
            self._zc.register_service(self._info)
        except Exception as exc:  # noqa: BLE001
            logger.warning("collab: mDNS register failed: %s", exc)
            self.discovery_error.emit(f"注册失败:{exc}")

        handler = _BrowserHandler(
            local_ip=local_ip,
            local_port=self._port,
            on_found=lambda ip, port, hn: self.peer_found.emit(ip, port, hn),
            on_lost=lambda ip, port: self.peer_lost.emit(ip, port),
        )
        self._browser = ServiceBrowser(self._zc, _MDNS_SERVICE_TYPE, handler)

        # Block until stop() is called
        self._browser._handlers_lock = getattr(self._browser, "_handlers_lock", threading.Event())
        while not self.isInterruptionRequested():
            time.sleep(0.5)

    def stop(self) -> None:
        self.requestInterruption()
        if self._zc:
            try:
                if self._info:
                    self._zc.unregister_service(self._info)
                self._zc.close()
            except Exception:  # noqa: BLE001
                pass
        self.quit()
        self.wait(3000)


class _BrowserHandler:
    """zeroconf ServiceBrowser callback adapter."""

    def __init__(self, local_ip: str, local_port: int,
                 on_found: Callable, on_lost: Callable) -> None:
        self._local_ip = local_ip
        self._local_port = local_port
        self._on_found = on_found
        self._on_lost = on_lost

    def add_service(self, zeroconf: Any, service_type: str, name: str) -> None:
        info = zeroconf.get_service_info(service_type, name)
        if not info:
            return
        ips = info.parsed_scoped_addresses()
        if not ips:
            return
        ip = ips[0]
        port = info.port
        # A host may have another/stale app instance listening on a neighbouring
        # port (for example current=5051 while an old process still owns 5050).
        # It is still this machine, not a collaboration peer.
        if ip == self._local_ip:
            return   # skip self on every port
        hostname = (info.properties.get(b"hostname") or b"").decode("utf-8", errors="replace")
        self._on_found(ip, port, hostname)

    def update_service(self, *_: Any) -> None:
        pass

    def remove_service(self, zeroconf: Any, service_type: str, name: str) -> None:
        info = zeroconf.get_service_info(service_type, name)
        if info:
            ips = info.parsed_scoped_addresses()
            if ips:
                self._on_lost(ips[0], info.port)
