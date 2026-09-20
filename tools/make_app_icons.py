#!/usr/bin/env python3
"""Render the rider app's PNG icons (standard library only, like the rest of the
platform side). Android's install prompt and iOS's home screen both want PNG;
app/icons/icon.svg is the same drawing for everything that accepts SVG.

    python3 tools/make_app_icons.py        # rewrites app/icons/*.png

The drawing: the app's half-circle fatigue gauge at 60 %, on the dark surface.
"""
import math
import os
import struct
import zlib

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "icons")
BACKGROUND, TRACK, VALUE, HUB = (13, 13, 13), (56, 56, 53), (57, 135, 229), (255, 255, 255)
# Geometry on a 512 grid — keep in sync with icon.svg.
CX, CY, RADIUS, STROKE, HUB_RADIUS, CORNER, FRACTION = 256.0, 316.0, 140.0, 44.0, 26.0, 112.0, 0.6


def arc_distance(x, y, start, end):
    """Distance from (x, y) to the arc between angles start > end (radians, y up)."""
    angle = math.atan2(CY - y, x - CX)
    if end <= angle <= start:
        return abs(math.hypot(x - CX, y - CY) - RADIUS)
    return min(math.hypot(x - (CX + RADIUS * math.cos(a)), y - (CY - RADIUS * math.sin(a))) for a in (start, end))


def rounded_square_distance(x, y, corner):
    dx, dy = abs(x - 256.0) - (256.0 - corner), abs(y - 256.0) - (256.0 - corner)
    return math.hypot(max(dx, 0.0), max(dy, 0.0)) + min(max(dx, dy), 0.0) - corner


def render(size, maskable):
    scale = 512.0 / size
    coverage = lambda distance: min(1.0, max(0.0, 0.5 - distance / scale))  # 1 px anti-aliased edge
    mix = lambda under, over, a: tuple(u + (o - u) * a for u, o in zip(under, over))
    rows = []
    for py in range(size):
        row = bytearray([0])  # PNG filter type: none
        for px in range(size):
            x, y = (px + 0.5) * scale, (py + 0.5) * scale
            alpha = 1.0 if maskable else coverage(rounded_square_distance(x, y, CORNER))  # maskable = full bleed
            colour = mix(BACKGROUND, TRACK, coverage(arc_distance(x, y, math.pi, 0.0) - STROKE / 2))
            colour = mix(colour, VALUE, coverage(arc_distance(x, y, math.pi, math.pi * (1 - FRACTION)) - STROKE / 2))
            colour = mix(colour, HUB, coverage(math.hypot(x - CX, y - CY) - HUB_RADIUS))
            row += bytes(int(round(c)) for c in colour) + bytes([int(round(alpha * 255))])
        rows.append(bytes(row))
    return b"".join(rows)


def write_png(path, size, maskable=False):
    def chunk(kind, data):
        body = kind + data
        return struct.pack("!I", len(data)) + body + struct.pack("!I", zlib.crc32(body))

    header = struct.pack("!IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                + chunk(b"IDAT", zlib.compress(render(size, maskable), 9)) + chunk(b"IEND", b""))
    print(f"{path}  {size}x{size}")


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    write_png(os.path.join(OUT, "icon-192.png"), 192)
    write_png(os.path.join(OUT, "icon-512.png"), 512)
    write_png(os.path.join(OUT, "icon-maskable-512.png"), 512, maskable=True)
