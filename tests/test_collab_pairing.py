"""test_collab_pairing.py — pairing-code encode/decode (no IP knowledge needed).

A host shows a short pairing code encoding ip:port:group_code; a joiner pastes
it to connect + adopt the group in one step.  Pure functions, fully testable.

Run:
    python3 -m pytest tests/test_collab_pairing.py -v
"""
from __future__ import annotations

import pytest

from app.widgets.collab_pairing import (
    PairingInfo,
    decode_pairing,
    encode_pairing,
    qr_available,
)


class TestRoundTrip:
    def test_encode_decode_round_trip(self):
        code = encode_pairing("192.168.1.42", 5050, "SMW-2026")
        info = decode_pairing(code)
        assert info == PairingInfo(ip="192.168.1.42", port=5050, group_code="SMW-2026")

    def test_code_is_compact_string(self):
        code = encode_pairing("10.0.0.1", 5051, "G")
        assert isinstance(code, str)
        assert code  # non-empty
        assert "\n" not in code

    def test_empty_group_round_trips(self):
        info = decode_pairing(encode_pairing("10.0.0.1", 5050, ""))
        assert info.group_code == ""
        assert info.port == 5050


class TestInvalid:
    def test_garbage_raises_value_error(self):
        with pytest.raises(ValueError):
            decode_pairing("not-a-valid-code!!!")

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            decode_pairing("")

    def test_wrong_prefix_raises(self):
        import base64
        bad = base64.urlsafe_b64encode(b"other|1.2.3.4|5050|g").decode()
        with pytest.raises(ValueError):
            decode_pairing(bad)


class TestQrGuard:
    def test_qr_available_returns_bool(self):
        assert isinstance(qr_available(), bool)
