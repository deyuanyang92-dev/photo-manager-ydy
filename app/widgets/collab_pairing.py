"""collab_pairing.py — pairing-code encode/decode for LAN collaboration.

A host displays a short pairing code (and optional QR) that encodes its
``ip:port:group_code``.  A joiner pastes the code to connect to the host and
adopt the same collaboration group in one step — no IP knowledge required.

The encode/decode functions are pure (no Qt).  QR rendering is optional and
guarded via :func:`qr_available` following the project's optional-dependency
pattern; callers fall back to the text code when QR is unavailable.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Optional

_PREFIX = "specimen"


@dataclass(frozen=True)
class PairingInfo:
    ip: str
    port: int
    group_code: str


def encode_pairing(ip: str, port: int, group_code: str) -> str:
    """Encode connection details into a compact, copy-pasteable code."""
    raw = f"{_PREFIX}|{ip}|{int(port)}|{group_code}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_pairing(code: str) -> PairingInfo:
    """Decode a pairing code.  Raises ValueError on any malformed input."""
    if not code or not code.strip():
        raise ValueError("空配对码")
    try:
        raw = base64.urlsafe_b64decode(code.strip().encode("ascii"))
        text = raw.decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        raise ValueError("配对码格式错误") from exc
    parts = text.split("|")
    if len(parts) != 4 or parts[0] != _PREFIX:
        raise ValueError("配对码无效")
    _, ip, port_s, group = parts
    try:
        port = int(port_s)
    except ValueError as exc:
        raise ValueError("配对码端口无效") from exc
    if not ip:
        raise ValueError("配对码缺少地址")
    return PairingInfo(ip=ip, port=port, group_code=group)


def qr_available() -> bool:
    """True when the optional ``qrcode`` package is importable."""
    import importlib.util
    return importlib.util.find_spec("qrcode") is not None


def make_qr_pixmap(code: str, size: int = 220) -> Optional["object"]:
    """Render *code* to a QPixmap QR, or return None if qrcode is unavailable.

    Imported lazily so the module stays usable (text-only) without qrcode/Qt.
    """
    if not qr_available():
        return None
    try:
        import qrcode
        from PyQt6.QtGui import QImage, QPixmap

        qr = qrcode.QRCode(border=2)
        qr.add_data(code)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        n = len(matrix)
        if n == 0:
            return None
        scale = max(1, size // n)
        img = QImage(n * scale, n * scale, QImage.Format.Format_RGB32)
        white, black = 0xFFFFFFFF, 0xFF000000
        for y in range(n):
            for x in range(n):
                color = black if matrix[y][x] else white
                for dy in range(scale):
                    for dx in range(scale):
                        img.setPixel(x * scale + dx, y * scale + dy, color)
        return QPixmap.fromImage(img)
    except Exception:  # noqa: BLE001
        return None
