"""memory_profile.py — runtime cache limits for low-RAM / performance mode.

Called once at startup from main.py (after settings are loaded).  Does not
change behaviour — only caps in-memory caches so idle RSS stays lower on
2 GB machines.  When a cache fills, thumbnails re-decode (slightly slower
scroll, same pixels).
"""
from __future__ import annotations

import os
import sys

THUMB_CACHE_LIMIT = 384
MONITOR_THUMB_CACHE_LIMIT = 512
QPIXMAP_CACHE_KB = 8192
MONITOR_THUMB_BATCH_SIZE = 4
LOW_MEMORY_GB = 3.5


def physical_memory_gb() -> float | None:
    """Total physical RAM in GB; None when detection fails."""
    try:
        if sys.platform == "win32":
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return None
            return stat.ullTotalPhys / (1024 ** 3)
        page_size = os.sysconf("SC_PAGE_SIZE")
        page_count = os.sysconf("SC_PHYS_PAGES")
        return (page_size * page_count) / (1024 ** 3)
    except Exception:
        return None


def is_low_memory_machine() -> bool:
    total = physical_memory_gb()
    return total is not None and total <= LOW_MEMORY_GB


def apply_memory_profile(*, performance_mode: bool = False) -> None:
    """Tune process-wide cache ceilings."""
    global THUMB_CACHE_LIMIT, MONITOR_THUMB_CACHE_LIMIT, QPIXMAP_CACHE_KB
    global MONITOR_THUMB_BATCH_SIZE
    if performance_mode:
        THUMB_CACHE_LIMIT = 192
        MONITOR_THUMB_CACHE_LIMIT = 256
        QPIXMAP_CACHE_KB = 4096
        MONITOR_THUMB_BATCH_SIZE = 1
    else:
        THUMB_CACHE_LIMIT = 384
        MONITOR_THUMB_CACHE_LIMIT = 512
        QPIXMAP_CACHE_KB = 8192
        MONITOR_THUMB_BATCH_SIZE = 4
