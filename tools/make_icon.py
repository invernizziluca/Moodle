"""Generate the app icon (PNG + multi-size ICO) without third-party libraries.

Design: FFHS red rounded square with a white archive box and a red down arrow.
"""

import struct
import zlib
from pathlib import Path

RED = (213, 0, 6)
WHITE = (255, 255, 255)
OUT = Path(__file__).resolve().parent.parent / "ffhs_archiver" / "assets"


def rounded_rect(x, y, x0, y0, x1, y1, r):
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def triangle(x, y, a, b, c):
    def sign(p1, p2, p3):
        return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])
    d1, d2, d3 = sign((x, y), a, b), sign((x, y), b, c), sign((x, y), c, a)
    neg = d1 < 0 or d2 < 0 or d3 < 0
    pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (neg and pos)


def shade(u, v):
    """Colour at normalised coordinates (0..1). Returns (r, g, b, a) or None for transparent."""
    if not rounded_rect(u, v, 0.02, 0.02, 0.98, 0.98, 0.20):
        return None
    color = RED
    # lid
    if rounded_rect(u, v, 0.16, 0.24, 0.84, 0.38, 0.035):
        color = WHITE
    # box body
    if rounded_rect(u, v, 0.21, 0.42, 0.79, 0.82, 0.05):
        color = WHITE
    # red down arrow on the body
    if 0.455 <= u <= 0.545 and 0.47 <= v <= 0.62:
        color = RED
    if triangle(u, v, (0.36, 0.60), (0.64, 0.60), (0.50, 0.75)):
        color = RED
    return color + (255,)


def render(size: int, ss: int = 4) -> bytes:
    rows = []
    for py in range(size):
        row = bytearray([0])
        for px in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    c = shade((px + (sx + 0.5) / ss) / size, (py + (sy + 0.5) / ss) / size)
                    if c:
                        acc[0] += c[0] * c[3]
                        acc[1] += c[1] * c[3]
                        acc[2] += c[2] * c[3]
                        acc[3] += c[3]
            n = ss * ss
            a = acc[3] / n
            if acc[3]:
                row += bytes((round(acc[0] / acc[3]), round(acc[1] / acc[3]), round(acc[2] / acc[3]), round(a)))
            else:
                row += bytes((0, 0, 0, 0))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    pngs = {s: render(s) for s in sizes}
    (OUT / "app.png").write_bytes(pngs[64])
    (OUT / "app_256.png").write_bytes(pngs[256])
    header = struct.pack("<HHH", 0, 1, len(sizes))
    entries, data = b"", b""
    offset = 6 + 16 * len(sizes)
    for s in sizes:
        png = pngs[s]
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    (OUT / "app.ico").write_bytes(header + entries + data)
    print("icons written to", OUT)


if __name__ == "__main__":
    main()
