"""test_collab_api.py — Security gate tests for the collab FastAPI endpoints.

Focus: the LAN trust boundary.  File download / manifest previously relied on
groupCode alone; they now also require the caller's IP to be inside the LAN
(loopback / RFC1918 / link-local), mirroring the activity endpoint.

Run:
    QT_QPA_PLATFORM=offscreen pytest tests/test_collab_api.py -v
"""
from __future__ import annotations

import pytest

from app.services.collab_api import _is_private_lan_host


class TestIsPrivateLanHost:
    @pytest.mark.parametrize("host", [
        "127.0.0.1",          # IPv4 loopback
        "::1",                # IPv6 loopback
        "10.0.0.5",           # RFC1918 class A
        "172.16.4.20",        # RFC1918 class B
        "192.168.1.30",       # RFC1918 class C
        "169.254.1.1",        # link-local
    ])
    def test_lan_addresses_trusted(self, host):
        assert _is_private_lan_host(host) is True

    @pytest.mark.parametrize("host", [
        "8.8.8.8",            # public DNS
        "1.1.1.1",            # public DNS
        "114.114.114.114",    # public CN resolver
        "93.184.216.34",      # example.com (truly globally routable)
    ])
    def test_public_addresses_rejected(self, host):
        assert _is_private_lan_host(host) is False

    def test_garbage_rejected(self):
        assert _is_private_lan_host("not-an-ip") is False
        assert _is_private_lan_host("") is False
        assert _is_private_lan_host(None) is False  # type: ignore[arg-type]
