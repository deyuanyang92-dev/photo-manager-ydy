"""Smoke-test NIIMBOT B203 direct USB printing.

Example:
    python scripts/niimbot_test_print.py --port COM5 --text "B203 TEST"
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.niimbot_print_service import (  # noqa: E402
    B203_MAX_WIDTH_PX,
    DEFAULT_DENSITY,
    detect_niimbot_ports,
    print_image_to_niimbot,
)


def _make_test_label(text: str, subtext: str, width: int, height: int) -> Image.Image:
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width - 1, height - 1), outline="black", width=2)
    draw.text((12, 12), text, fill="black")
    draw.text((12, 44), subtext, fill="black")
    draw.line((12, height - 18, width - 12, height - 18), fill="black", width=2)
    return image


def main() -> int:
    parser = argparse.ArgumentParser(description="Print a NIIMBOT B203 USB test label.")
    parser.add_argument("--port", default="", help="COM port, for example COM5. Default: auto-detect.")
    parser.add_argument("--text", default="NIIMBOT B203", help="Main test label text.")
    parser.add_argument("--density", type=int, default=DEFAULT_DENSITY, choices=range(1, 6))
    parser.add_argument("--width", type=int, default=240, help="Bitmap width in pixels.")
    parser.add_argument("--height", type=int, default=120, help="Bitmap height in pixels.")
    parser.add_argument("--rotate", type=int, default=0, choices=(0, 90, 180, 270))
    args = parser.parse_args()

    port = args.port.strip()
    if not port:
        ports = detect_niimbot_ports()
        if not ports:
            print("No NIIMBOT B203 USB serial port detected.", file=sys.stderr)
            return 2
        port = ports[0]

    image = _make_test_label(args.text, f"{port} / {args.width}x{args.height}", args.width, args.height)
    if args.rotate in (90, 270) and args.height > B203_MAX_WIDTH_PX:
        print(f"Rotated image would exceed B203 width limit: {args.height}px", file=sys.stderr)
        return 2
    if args.rotate in (0, 180) and args.width > B203_MAX_WIDTH_PX:
        print(f"Image exceeds B203 width limit: {args.width}px", file=sys.stderr)
        return 2

    print_image_to_niimbot(image, port=port, density=args.density, rotate=args.rotate)
    print(f"Printed NIIMBOT B203 test label on {port}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
