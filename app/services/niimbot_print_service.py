"""Direct USB printing for NIIMBOT B203 label printers.

The B203 does not install as a normal Windows printer queue on this machine.
It exposes a USB serial device instead, so Qt's ``QPrinter`` path cannot see
it.  This module renders existing label jobs to monochrome bitmaps and sends
the public NIIMBOT packet protocol over the detected COM port.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import math
from pathlib import Path
import re
import struct
import tempfile
import time
from typing import Callable, Iterable, Optional

from PIL import Image, ImageDraw, ImageFont


PRINTER_ID_PREFIX = "NIIMBOT:B203:"
DEFAULT_DPI = 203
DEFAULT_DENSITY = 3
DEFAULT_BAUDRATE = 115200
B203_MAX_LABEL_WIDTH_MM = 52
B203_MAX_WIDTH_PX = round(B203_MAX_LABEL_WIDTH_MM * DEFAULT_DPI / 25.4)
B203_LABEL_WIDTH_MM = 40
B203_LABEL_HEIGHT_MM = 30
B203_LABEL_WIDTH_PX = round(B203_LABEL_WIDTH_MM * DEFAULT_DPI / 25.4)
B203_LABEL_HEIGHT_PX = round(B203_LABEL_HEIGHT_MM * DEFAULT_DPI / 25.4)
B203_THRESHOLD = 168
B203_MEDIA_NOTICE = "B203 直连会按 T40×30mm 白色间隙纸逐张打印；请确认官方 NIIMBOT App 未连接设备。"

_B203_TEMPLATE = {
    "name": "NIIMBOT B203 40x30",
    "code": "NII-B203-40",
    "minSize": {"w": B203_LABEL_WIDTH_MM, "h": B203_LABEL_HEIGHT_MM},
    "lineHeight": 1.08,
    "rows": [
        {"fields": [{"key": "b203Uid1", "style": "bold", "size": 6.2}], "wrap": False},
        {"fields": [{"key": "b203Uid2", "style": "bold", "size": 6.2}], "wrap": False},
        {"fields": [{"key": "b203Meta", "size": 5.2}], "wrap": False},
    ],
    "qr": {"content": "uniqueId", "position": "bottom", "sizePct": 0.34, "ecc": "M"},
}
_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\arial.ttf",
)


class NiimbotPrintError(RuntimeError):
    """Raised when a NIIMBOT direct-print operation cannot complete."""


@dataclass(frozen=True)
class NiimbotPrinter:
    """Virtual printer entry shown beside system printers."""

    id: str
    name: str
    port: str
    model: str = "B203"


@dataclass(frozen=True)
class _Packet:
    type: int
    data: bytes = b""

    @classmethod
    def from_bytes(cls, raw: bytes) -> "_Packet":
        if len(raw) < 7 or raw[:2] != b"\x55\x55" or raw[-2:] != b"\xaa\xaa":
            raise NiimbotPrintError("invalid NIIMBOT packet frame")
        cmd = raw[2]
        n = raw[3]
        if len(raw) != n + 7:
            raise NiimbotPrintError("invalid NIIMBOT packet length")
        data = raw[4 : 4 + n]
        checksum = cmd ^ n
        for b in data:
            checksum ^= b
        if checksum != raw[-3]:
            raise NiimbotPrintError("invalid NIIMBOT packet checksum")
        return cls(cmd, bytes(data))

    def to_bytes(self) -> bytes:
        if not 0 <= int(self.type) <= 255:
            raise NiimbotPrintError("NIIMBOT command out of range")
        if len(self.data) > 255:
            raise NiimbotPrintError("NIIMBOT packet payload is too large")
        checksum = int(self.type) ^ len(self.data)
        for b in self.data:
            checksum ^= b
        return bytes((0x55, 0x55, int(self.type), len(self.data), *self.data, checksum, 0xAA, 0xAA))


def printer_id(port: str) -> str:
    return f"{PRINTER_ID_PREFIX}{str(port or '').strip()}"


def is_niimbot_printer_id(value: str) -> bool:
    return str(value or "").strip().upper().startswith(PRINTER_ID_PREFIX)


def port_from_printer_id(value: str) -> str:
    raw = str(value or "").strip()
    if not is_niimbot_printer_id(raw):
        return ""
    return raw[len(PRINTER_ID_PREFIX) :].strip()


def display_name(port: str) -> str:
    return f"NIIMBOT B203 USB ({str(port or '').strip() or 'auto'})"


def available_printers() -> list[NiimbotPrinter]:
    return [
        NiimbotPrinter(id=printer_id(port), name=display_name(port), port=port)
        for port in detect_niimbot_ports()
    ]


def available_printer_ids() -> set[str]:
    return {p.id for p in available_printers()}


def compatibility_notice(paper_types: Iterable[str] | None = None) -> str:
    """Human-facing reminder for the B203 virtual printer route."""
    values = {str(v or "").strip().lower() for v in (paper_types or []) if str(v or "").strip()}
    notice = B203_MEDIA_NOTICE
    if values & {"a4", "a5"}:
        notice += " 当前纸张设置含 A4/A5；选择 B203 时不会合版，会自动转为 40×30mm 单张标签。"
    return notice


def detect_niimbot_ports(comports_fn: Optional[Callable[[], Iterable[object]]] = None) -> list[str]:
    """Return COM devices that look like NIIMBOT USB serial interfaces."""
    if comports_fn is None:
        try:
            comports_fn = _serial_comports
        except NiimbotPrintError:
            return []

    ports: list[str] = []
    try:
        candidates = list(comports_fn())
    except Exception:
        return []
    for item in candidates:
        device = str(getattr(item, "device", "") or (item[0] if isinstance(item, tuple) and item else "")).strip()
        if not device:
            continue
        desc = str(getattr(item, "description", "") or (item[1] if isinstance(item, tuple) and len(item) > 1 else ""))
        hwid = str(getattr(item, "hwid", "") or (item[2] if isinstance(item, tuple) and len(item) > 2 else ""))
        vid = getattr(item, "vid", None)
        pid = getattr(item, "pid", None)
        haystack = f"{device} {desc} {hwid}".lower()
        is_known_usb = vid == 0x3513 and pid == 0x0002
        is_named = "niimbot" in haystack or "vid:pid=3513:0002" in haystack
        if is_known_usb or is_named:
            ports.append(device)
    return sorted(dict.fromkeys(ports), key=_port_sort_key)


def print_jobs_to_niimbot(
    jobs: list[dict],
    *,
    printer_name: str,
    document_name: str = "Specimen labels",
    density: int = DEFAULT_DENSITY,
    dpi: int = DEFAULT_DPI,
    rotate: int = 0,
    cut_marks: bool = False,
    draw_crop_marks: Optional[Callable] = None,
) -> str:
    """Render label jobs and send them to a detected NIIMBOT printer.

    Returns the display name of the printer used.  A4/A5 imposition is rejected
    because the B203 is a roll-label printer, not a sheet printer.
    """
    printable = [job for job in jobs if job and (job.get("items") or [])]
    if not printable:
        raise NiimbotPrintError("no labels to print")
    port = _resolve_port(printer_name)
    images = list(_render_b203_job_images(printable))
    if not images:
        raise NiimbotPrintError("no printable label pages were generated")
    for image in images:
        print_image_to_niimbot(image, port=port, density=density, rotate=rotate)
    return display_name(port)


def print_image_to_niimbot(
    image: Image.Image,
    *,
    port: str,
    density: int = DEFAULT_DENSITY,
    rotate: int = 0,
    max_width_px: int = B203_MAX_WIDTH_PX,
) -> None:
    """Send a PIL image to the B203 over USB serial."""
    try:
        rotate = int(rotate)
    except (TypeError, ValueError):
        rotate = 0
    if rotate not in {0, 90, 180, 270}:
        raise NiimbotPrintError("NIIMBOT rotation must be 0, 90, 180, or 270 degrees")
    if rotate:
        image = image.rotate(-rotate, expand=True)
    image = image.convert("RGB")
    image = _fit_image_to_width(image, max_width_px)
    density = max(1, min(5, int(density)))

    serial_mod = _serial_module()
    try:
        with serial_mod.Serial(
            port=port,
            baudrate=DEFAULT_BAUDRATE,
            timeout=0.5,
            write_timeout=2,
        ) as ser:
            _NiimbotClient(ser).print_image(image, density=density)
    except NiimbotPrintError:
        raise
    except Exception as exc:
        raise NiimbotPrintError(f"NIIMBOT B203 print failed on {port}: {exc}") from exc


def _resolve_port(value: str) -> str:
    port = port_from_printer_id(value) or str(value or "").strip()
    if port and port.lower() != "auto":
        return port
    ports = detect_niimbot_ports()
    if not ports:
        raise NiimbotPrintError("NIIMBOT B203 USB printer was not detected")
    return ports[0]


def _fit_image_to_width(image: Image.Image, max_width_px: int) -> Image.Image:
    """Scale oversized labels down to the B203 printable width."""
    max_width_px = max(1, int(max_width_px))
    if image.width <= max_width_px:
        return image
    ratio = max_width_px / float(image.width)
    new_size = (max_width_px, max(1, round(image.height * ratio)))
    return image.resize(new_size, Image.Resampling.LANCZOS)


def _fit_image_to_paper(image: Image.Image, width_px: int, height_px: int) -> Image.Image:
    """Fit any rendered page onto the detected B203 label stock."""
    width_px = max(1, int(width_px))
    height_px = max(1, int(height_px))
    if image.size == (width_px, height_px):
        return image
    scale = min(width_px / float(image.width), height_px / float(image.height))
    new_size = (
        max(1, min(width_px, round(image.width * scale))),
        max(1, min(height_px, round(image.height * scale))),
    )
    fitted = image.resize(new_size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (width_px, height_px), "white")
    offset = ((width_px - new_size[0]) // 2, (height_px - new_size[1]) // 2)
    canvas.paste(fitted, offset)
    return canvas


def _prepare_b203_jobs(jobs: list[dict]) -> list[dict]:
    """Use a compact 40x30 label format regardless of the selected big template."""
    prepared: list[dict] = []
    for job in jobs:
        next_job = copy.deepcopy(job)
        next_job["paperType"] = "label"
        next_job["paper"] = None
        next_job["dims"] = {"w": B203_LABEL_WIDTH_MM, "h": B203_LABEL_HEIGHT_MM}
        next_job["template"] = copy.deepcopy(_B203_TEMPLATE)
        next_items = []
        for item in next_job.get("items") or []:
            if not isinstance(item, dict):
                next_items.append(item)
                continue
            next_item = dict(item)
            data = dict(next_item.get("data") or {})
            next_item["data"] = _augment_b203_label_data(data)
            next_items.append(next_item)
        next_job["items"] = next_items
        next_job["labels"] = [
            item.get("data")
            for item in next_items
            if isinstance(item, dict) and isinstance(item.get("data"), dict)
        ]
        prepared.append(next_job)
    return prepared


def _render_b203_job_images(jobs: list[dict]) -> Iterable[Image.Image]:
    for job in jobs:
        data_rows = job.get("labels") or [
            item.get("data")
            for item in (job.get("items") or [])
            if isinstance(item, dict)
        ]
        for data in data_rows:
            if not isinstance(data, dict) or not data:
                continue
            yield _render_b203_label_image(_augment_b203_label_data(data))


def _render_b203_label_image(data: dict) -> Image.Image:
    image = Image.new("RGB", (B203_LABEL_WIDTH_PX, B203_LABEL_HEIGHT_PX), "white")
    draw = ImageDraw.Draw(image)
    font_path = _b203_font_path()

    margin = 10
    qr_size = 94
    qr_y = B203_LABEL_HEIGHT_PX - qr_size - 10
    qr_x = (B203_LABEL_WIDTH_PX - qr_size) // 2
    text_w = B203_LABEL_WIDTH_PX - margin * 2

    _draw_fit_text(draw, str(data.get("b203Uid1") or ""), (margin, 10), text_w, font_path, 25, 14)
    _draw_fit_text(draw, str(data.get("b203Uid2") or ""), (margin, 40), text_w, font_path, 25, 14)
    _draw_fit_text(draw, str(data.get("b203Meta") or ""), (margin, 72), text_w, font_path, 19, 11)

    qr_text = str(data.get("uniqueId") or data.get("headerId") or data.get("b203Uid1") or "")
    qr = _make_qr_image(qr_text, qr_size)
    if qr is not None:
        image.paste(qr, (qr_x, qr_y))

    return image


def _draw_fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    xy: tuple[int, int],
    max_width: int,
    font_path: str,
    max_size: int,
    min_size: int,
) -> None:
    text = str(text or "")
    if not text:
        return
    for size in range(int(max_size), int(min_size) - 1, -1):
        font = _load_font(font_path, size)
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_width or size == min_size:
            draw.text(xy, text, fill="black", font=font)
            return


def _load_font(font_path: str, size: int):
    try:
        if font_path:
            return ImageFont.truetype(font_path, size=size)
    except Exception:
        pass
    try:
        return ImageFont.truetype("arial.ttf", size=size)
    except Exception:
        return ImageFont.load_default()


def _b203_font_path() -> str:
    for raw in _FONT_CANDIDATES:
        path = Path(raw)
        if path.exists():
            return str(path)
    return ""


def _make_qr_image(text: str, size_px: int) -> Optional[Image.Image]:
    if not text:
        return None
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M
    except Exception:
        return None
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=4,
        border=1,
    )
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    return img.resize((size_px, size_px), Image.Resampling.NEAREST)


def _augment_b203_label_data(data: dict) -> dict:
    uid = str(data.get("headerId") or data.get("uniqueId") or "").strip()
    line1, line2 = _split_uid_for_b203(uid)
    meta_parts = [
        str(data.get("storage") or "").strip(),
        str(data.get("shortDate") or "").strip(),
    ]
    species = str(data.get("speciesName") or data.get("species") or "").strip()
    meta = " ".join(part for part in meta_parts if part)
    if species:
        meta = f"{meta} {species}".strip()
    out = dict(data)
    out["b203Uid1"] = line1
    out["b203Uid2"] = line2
    out["b203Meta"] = meta
    return out


def _split_uid_for_b203(uid: str) -> tuple[str, str]:
    uid = str(uid or "").strip()
    if len(uid) <= 18:
        return uid, ""
    parts = uid.split("-")
    if len(parts) > 1:
        best = parts[0]
        rest = "-".join(parts[1:])
        for i in range(2, len(parts) + 1):
            left = "-".join(parts[:i])
            right = "-".join(parts[i:])
            if len(left) <= 20:
                best, rest = left, right
        if rest:
            return best, rest
    return uid[:20], uid[20:]


def _render_jobs_to_pages(output_jobs: list[dict], output_dir: Path, **kwargs) -> list[dict]:
    from app.utils.windows_print import render_jobs_to_pages

    return render_jobs_to_pages(output_jobs, output_dir, **kwargs)


def _serial_module():
    try:
        import serial
    except ImportError as exc:
        raise NiimbotPrintError("pyserial is required for NIIMBOT B203 direct printing") from exc
    return serial


def _serial_comports() -> Iterable[object]:
    try:
        from serial.tools import list_ports
    except ImportError as exc:
        raise NiimbotPrintError("pyserial is required for NIIMBOT B203 detection") from exc
    return list_ports.comports()


def _port_sort_key(port: str) -> tuple[str, int, str]:
    m = re.fullmatch(r"([A-Za-z]+)(\d+)", str(port or ""))
    if m:
        return (m.group(1).upper(), int(m.group(2)), "")
    return ("", 0, str(port or ""))


class _NiimbotClient:
    def __init__(self, serial_port) -> None:
        self._serial = serial_port
        self._packetbuf = bytearray()

    def print_image(self, image: Image.Image, *, density: int) -> None:
        self._require(self._set_label_density(density), "set density")
        self._require(self._set_label_type(1), "set label type")
        self._require(self._start_print(), "start print")
        self._require(self._start_page_print(), "start page")
        self._require(self._set_dimension(image.height, image.width), "set dimensions")
        for packet in self._encode_image(image):
            self._send(packet)
        self._require(self._end_page_print(), "end page")
        time.sleep(0.3)
        for _ in range(30):
            if self._end_print():
                return
            time.sleep(0.1)
        raise NiimbotPrintError("NIIMBOT did not finish the print job")

    def _send(self, packet: _Packet) -> None:
        self._serial.write(packet.to_bytes())

    def _recv(self) -> list[_Packet]:
        self._packetbuf.extend(self._serial.read(1024))
        packets: list[_Packet] = []
        while len(self._packetbuf) > 4:
            if self._packetbuf[:2] != b"\x55\x55":
                del self._packetbuf[0]
                continue
            packet_len = self._packetbuf[3] + 7
            if len(self._packetbuf) < packet_len:
                break
            raw = bytes(self._packetbuf[:packet_len])
            packets.append(_Packet.from_bytes(raw))
            del self._packetbuf[:packet_len]
        return packets

    def _transceive(self, command: int, data: bytes, response_offset: int = 1) -> Optional[_Packet]:
        response_command = int(command) + int(response_offset)
        self._send(_Packet(command, data))
        for _ in range(6):
            for packet in self._recv():
                if packet.type == 0xDB:
                    raise NiimbotPrintError("NIIMBOT reported a printer error")
                if packet.type == response_command:
                    return packet
            time.sleep(0.1)
        return None

    def _set_label_density(self, density: int) -> bool:
        return self._ok(self._transceive(0x21, bytes((density,)), 16))

    def _set_label_type(self, label_type: int) -> bool:
        return self._ok(self._transceive(0x23, bytes((label_type,)), 16))

    def _start_print(self) -> bool:
        return self._ok(self._transceive(0x01, b"\x01"))

    def _start_page_print(self) -> bool:
        return self._ok(self._transceive(0x03, b"\x01"))

    def _set_dimension(self, height: int, width: int) -> bool:
        # NIIMBOT command 0x13 expects printable height first, then row width.
        return self._ok(self._transceive(0x13, struct.pack(">HH", int(height), int(width))))

    def _end_page_print(self) -> bool:
        return self._ok(self._transceive(0xE3, b"\x01"))

    def _end_print(self) -> bool:
        return self._ok(self._transceive(0xF3, b"\x01"))

    @staticmethod
    def _ok(packet: Optional[_Packet]) -> bool:
        return bool(packet and packet.data and packet.data[0])

    @staticmethod
    def _require(ok: bool, step: str) -> None:
        if not ok:
            raise NiimbotPrintError(f"NIIMBOT failed to {step}")

    @staticmethod
    def _encode_image(image: Image.Image) -> Iterable[_Packet]:
        img = _thermal_black_mask(image)
        for y in range(img.height):
            bits = "".join("0" if img.getpixel((x, y)) == 0 else "1" for x in range(img.width))
            row = int(bits, 2).to_bytes(math.ceil(img.width / 8), "big") if bits else b""
            header = struct.pack(">H3BB", y, 0, 0, 0, 1)
            yield _Packet(0x85, header + row)


def _thermal_black_mask(image: Image.Image, threshold: int = B203_THRESHOLD) -> Image.Image:
    """Return a 1-bit mask where white=0 and printable black=255."""
    threshold = max(1, min(254, int(threshold)))
    gray = image.convert("L")
    return gray.point(lambda p: 255 if p < threshold else 0, mode="1")
