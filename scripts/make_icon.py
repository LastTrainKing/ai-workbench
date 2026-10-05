"""Generate the application icon (``assets/ai-workbench.ico``).

Written by hand rather than exported from a design tool so the icon can be
regenerated from source at any time, and so the repository carries no opaque
binary that nobody can reproduce.

The mark mirrors the product's identity: a dark rounded square with the indigo
accent used throughout the UI, and three connected nodes representing the
knowledge → task → execution loop.

Usage::

    python scripts/make_icon.py
"""

from __future__ import annotations

import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "assets" / "ai-workbench.ico"

SIZES = (16, 24, 32, 48, 64, 128, 256)

# Palette (matches tailwind.config.js / the CSS variables in the UI).
BACKGROUND = (11, 13, 16)      # #0b0d10
SURFACE = (23, 27, 33)         # #171b21
ACCENT = (99, 102, 241)        # #6366f1
ACCENT_LIGHT = (139, 92, 246)  # #8b5cf6
FOREGROUND = (230, 233, 239)   # #e6e9ef


def _rounded_alpha(x: float, y: float, size: float, radius: float) -> bool:
    """True when (x, y) is inside a rounded square of the given size/radius."""
    if x < 0 or y < 0 or x > size or y > size:
        return False
    cx = min(max(x, radius), size - radius)
    cy = min(max(y, radius), size - radius)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= radius * radius


def _blend(base: tuple[int, int, int], top: tuple[int, int, int], alpha: float):
    alpha = min(max(alpha, 0.0), 1.0)
    return tuple(round(b + (t - b) * alpha) for b, t in zip(base, top, strict=True))


def _line_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    cx, cy = ax + t * dx, ay + t * dy
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5


def render(size: int) -> bytes:
    """Render one square as BGRA rows (bottom-up, as BMP expects)."""
    scale = size / 64.0
    radius = 14 * scale
    stroke = max(1.0, 2.4 * scale)

    # The three loop nodes and the edges between them (64x64 design grid).
    top = (32 * scale, 18 * scale)
    left = (19 * scale, 45 * scale)
    right = (45 * scale, 45 * scale)
    edges = [(top, left), (top, right), (left, right)]

    rows: list[bytes] = []
    for y in range(size - 1, -1, -1):
        row = bytearray()
        for x in range(size):
            px, py = x + 0.5, y + 0.5

            if not _rounded_alpha(px, py, size, radius):
                row += b"\x00\x00\x00\x00"  # transparent outside the rounded square
                continue

            # Diagonal background gradient, subtle inner surface.
            t = (px + py) / (2.0 * size)
            colour = _blend(BACKGROUND, SURFACE, 0.35 + 0.4 * t)

            # Accent border.
            inner = _rounded_alpha(px, py, size, radius) and not _rounded_alpha(
                px, py, size - stroke * 2, max(0.0, radius - stroke)
            )
            if inner:
                colour = _blend(colour, ACCENT, 0.55)

            # Edges.
            for (ax, ay), (bx, by) in edges:
                if _line_distance(px, py, ax, ay, bx, by) <= stroke * 0.8:
                    colour = _blend(colour, ACCENT, 0.95)
                    break

            # Nodes: filled accent circles, the top one hollow with a light core.
            for index, (nx, ny) in enumerate((top, left, right)):
                distance = ((px - nx) ** 2 + (py - ny) ** 2) ** 0.5
                node_radius = (6.5 if index == 0 else 5.5) * scale
                if distance <= node_radius:
                    colour = _blend(colour, ACCENT_LIGHT, 1.0)
                elif index == 0 and distance <= node_radius * 0.42:
                    colour = _blend(colour, FOREGROUND, 1.0)

            row += bytes((colour[2], colour[1], colour[0], 255))
        rows.append(bytes(row))
    return b"".join(rows)


def bmp_for_ico(size: int) -> bytes:
    """A BMP payload as ICO expects it: BITMAPINFOHEADER with doubled height."""
    pixels = render(size)
    header = struct.pack(
        "<IiiHHIIiiII",
        40,          # biSize
        size,        # biWidth
        size * 2,    # biHeight (XOR + AND mask)
        1,           # biPlanes
        32,          # biBitCount
        0,           # biCompression = BI_RGB
        len(pixels), # biSizeImage
        0, 0, 0, 0,  # resolution + palette
    )
    # A 32-bit ICO needs an AND mask; all-zero means "use the alpha channel".
    mask_stride = ((size + 31) // 32) * 4
    return header + pixels + b"\x00" * (mask_stride * size)


def main() -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    images = [bmp_for_ico(size) for size in SIZES]

    with OUTPUT.open("wb") as handle:
        # ICONDIR
        handle.write(struct.pack("<HHH", 0, 1, len(images)))
        offset = 6 + 16 * len(images)
        for size, payload in zip(SIZES, images, strict=True):
            handle.write(
                struct.pack(
                    "<BBBBHHII",
                    0 if size >= 256 else size,  # width  (0 means 256)
                    0 if size >= 256 else size,  # height
                    0,                           # palette colours
                    0,                           # reserved
                    1,                           # colour planes
                    32,                          # bits per pixel
                    len(payload),
                    offset,
                )
            )
            offset += len(payload)
        for payload in images:
            handle.write(payload)

    print(f"  wrote {OUTPUT.relative_to(ROOT)} ({OUTPUT.stat().st_size / 1024:.1f} KB, {len(SIZES)} sizes)")
    return 0




def write_png(size: int, path: Path) -> None:
    """Emit one square as a PNG.

    Tauri's bundler needs a raster icon for several targets, and the ICO's BMP
    payload is not readable by every tool. Written by hand for the same reason as
    the ICO: the asset stays reproducible from source.
    """
    import struct
    import zlib

    pixels = render(size)
    rows = [pixels[i * size * 4 : (i + 1) * size * 4] for i in range(size)]
    rows.reverse()  # render() is bottom-up for BMP; PNG wants top-down

    raw = bytearray()
    for row in rows:
        raw.append(0)  # filter type: None
        for x in range(size):
            b, g, r, a = row[x * 4 : x * 4 + 4]
            raw += bytes((r, g, b, a))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    print(f"  wrote {path} ({len(png) / 1024:.1f} KB)")


def main_png() -> int:
    assets = ROOT / "assets"
    write_png(256, assets / "icon-256.png")
    write_png(128, assets / "icon-128.png")
    write_png(32, assets / "icon-32.png")
    return 0


if __name__ == "__main__":
    main()
    raise SystemExit(main_png())
