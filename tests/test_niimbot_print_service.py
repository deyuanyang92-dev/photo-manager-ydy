from dataclasses import dataclass

import pytest
from PIL import Image

from app.services import niimbot_print_service as svc


@dataclass
class _Port:
    device: str
    description: str
    hwid: str
    vid: int | None = None
    pid: int | None = None


def _job(paper_type="label"):
    return {
        "bucket": "sample",
        "items": [{"idx": 0, "data": {"uniqueId": "U1"}}],
        "labels": [{"uniqueId": "U1"}],
        "paperType": paper_type,
        "dims": {"w": 30, "h": 15},
        "template": {},
    }


def test_packet_roundtrip_and_checksum():
    raw = svc._Packet(0x21, b"\x03").to_bytes()
    assert raw == bytes.fromhex("55 55 21 01 03 23 aa aa")
    packet = svc._Packet.from_bytes(raw)
    assert packet.type == 0x21
    assert packet.data == b"\x03"


def test_detect_niimbot_ports_by_usb_vid_pid_and_name():
    ports = svc.detect_niimbot_ports(lambda: [
        _Port("COM1", "USB Serial", "USB VID:PID=1234:5678", 0x1234, 0x5678),
        _Port("COM10", "NIIMBOT B203", "USB VID:PID=3513:0002", None, None),
        _Port("COM5", "USB Serial Device", "USB VID:PID=3513:0002", 0x3513, 0x0002),
    ])

    assert ports == ["COM5", "COM10"]


def test_print_jobs_accepts_sheet_paper_as_b203_roll_labels(monkeypatch):
    captured = {}

    def fake_print(image, *, port, density, rotate):
        captured["size"] = image.size
        captured["port"] = port

    monkeypatch.setattr(svc, "print_image_to_niimbot", fake_print)

    used = svc.print_jobs_to_niimbot([_job("a4")], printer_name=svc.printer_id("COM5"))

    assert used == "NIIMBOT B203 USB (COM5)"
    assert captured["port"] == "COM5"
    assert captured["size"] == (svc.B203_LABEL_WIDTH_PX, svc.B203_LABEL_HEIGHT_PX)


def test_print_jobs_renders_b203_pil_images(monkeypatch, tmp_path):
    captured = {}

    def fake_print(image, *, port, density, rotate):
        captured["image_size"] = image.size
        hist = image.convert("L").histogram()
        captured["black_pixels"] = sum(hist[:128])
        captured["port"] = port
        captured["density"] = density
        captured["rotate"] = rotate

    monkeypatch.setattr(svc, "print_image_to_niimbot", fake_print)

    used = svc.print_jobs_to_niimbot(
        [_job()],
        printer_name=svc.printer_id("COM5"),
        density=4,
        rotate=90,
    )

    assert used == "NIIMBOT B203 USB (COM5)"
    assert captured["port"] == "COM5"
    assert captured["density"] == 4
    assert captured["rotate"] == 90
    assert captured["image_size"] == (svc.B203_LABEL_WIDTH_PX, svc.B203_LABEL_HEIGHT_PX)
    assert captured["black_pixels"] > 1000


def test_fit_image_to_width_scales_oversized_label():
    image = Image.new("RGB", (480, 240), "white")

    fitted = svc._fit_image_to_width(image, 416)

    assert fitted.size == (416, 208)


def test_fit_image_to_width_keeps_safe_label_size():
    image = Image.new("RGB", (400, 240), "white")

    assert svc._fit_image_to_width(image, 416) is image


def test_fit_image_to_paper_uses_fixed_40x30_canvas():
    image = Image.new("RGB", (480, 320), "white")

    fitted = svc._fit_image_to_paper(
        image,
        svc.B203_LABEL_WIDTH_PX,
        svc.B203_LABEL_HEIGHT_PX,
    )

    assert fitted.size == (svc.B203_LABEL_WIDTH_PX, svc.B203_LABEL_HEIGHT_PX)


def test_set_dimension_packet_uses_height_then_width(monkeypatch):
    client = svc._NiimbotClient(serial_port=object())
    captured = {}

    def fake_transceive(command, data, response_offset=1):
        captured["command"] = command
        captured["data"] = data
        captured["response_offset"] = response_offset
        return svc._Packet(command + response_offset, b"\x01")

    monkeypatch.setattr(client, "_transceive", fake_transceive)

    assert client._set_dimension(240, 320) is True
    assert captured == {
        "command": 0x13,
        "data": bytes.fromhex("00 f0 01 40"),
        "response_offset": 1,
    }


def test_prepare_b203_jobs_splits_long_uid_for_readability():
    job = _job()
    job["items"][0]["data"] = {
        "uniqueId": "FJ-YGLZ-B2-DLC001-RT95E-20260810-0812",
        "storage": "RNA",
        "shortDate": "260810",
    }

    prepared = svc._prepare_b203_jobs([job])[0]
    data = prepared["items"][0]["data"]

    assert data["b203Uid1"] == "FJ-YGLZ-B2-DLC001"
    assert data["b203Uid2"] == "RT95E-20260810-0812"
    assert "RNA" in data["b203Meta"]


def test_thermal_black_mask_threshold_has_no_gray_dither_noise():
    image = Image.new("L", (3, 1), 255)
    image.putpixel((0, 0), 0)
    image.putpixel((1, 0), 120)
    image.putpixel((2, 0), 220)

    mask = svc._thermal_black_mask(image)

    assert [mask.getpixel((x, 0)) for x in range(3)] == [255, 255, 0]
