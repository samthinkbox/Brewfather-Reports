"""Draw the app icon (a hop cone) and write it to templates/icons/.

    python tools/make_icons.py

Standard library only: a small rasteriser with supersampling, a PNG writer (zlib) and an
ICO writer (PNG entries). The files are committed, so this only needs running to change the icon.

    icon-192.png, icon-512.png   rounded square, for the browser's "Install app" and Linux menus
    icon-maskable-512.png        full square, for platforms that crop icons to their own shape
    app.ico                      16-256 px, for the Windows Start Menu and desktop shortcuts
"""
import math
import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
import paths  # noqa: E402

BG = (31, 77, 43)          # deep hop green
BRACT = (163, 214, 92)     # lime bract
EDGE = (60, 110, 34)       # bract outline
STEM = (163, 214, 92)

# bracts, drawn top row first so lower rows overlap the ones above, as on a real cone
ROWS = [(0.31, [0.5]), (0.40, [0.43, 0.57]), (0.49, [0.36, 0.5, 0.64]), (0.58, [0.43, 0.57]), (0.67, [0.5])]
RX, RY, TIP = 0.088, 0.075, 1.7   # bract half-width, half-height above the middle, point length below (x RY)


def shapes():
    s = [("rect", 0.485, 0.17, 0.515, 0.28, STEM),                    # stem
         ("ellipse", 0.60, 0.205, 0.085, 0.035, -0.45, STEM, None)]   # leaf
    for y, xs in ROWS:
        for x in xs:
            s.append(("bract", x, y, RX, RY, 0.0, BRACT, EDGE))
    return s


def in_bract(ux, uy, rx, ry):
    """A bract: round on top, coming to a point below."""
    if uy <= 0:
        return (ux / rx) ** 2 + (uy / ry) ** 2 <= 1
    t = uy / (ry * TIP)
    return t < 1 and abs(ux) <= rx * math.sqrt(1 - t) * (1 - t * 0.35)


def colour_at(x, y, rounded):
    """Colour (r, g, b, a) of one sample point in the unit square."""
    # background: rounded square (radius 0.2) or the full square
    if rounded:
        r = 0.2
        dx = max(r - x, 0, x - (1 - r))
        dy = max(r - y, 0, y - (1 - r))
        if dx * dx + dy * dy > r * r:
            return (0, 0, 0, 0)
    col = BG
    for sh in SHAPES:
        if sh[0] == "rect":
            _, x0, y0, x1, y1, c = sh
            if x0 <= x <= x1 and y0 <= y <= y1:
                col = c
        elif sh[0] == "bract":
            _, cx, cy, rx, ry, _, fill, edge = sh
            if in_bract(x - cx, y - cy, rx, ry):
                col = fill if in_bract(x - cx, y - cy + 0.004, rx - 0.012, ry - 0.010) else edge
        else:
            _, cx, cy, rx, ry, ang, fill, edge = sh
            ux, uy = x - cx, y - cy
            if abs(ux) > rx + ry or abs(uy) > rx + ry:
                continue
            if ang:
                ca, sa = math.cos(ang), math.sin(ang)
                ux, uy = ux * ca + uy * sa, -ux * sa + uy * ca
            f = (ux / rx) ** 2 + (uy / ry) ** 2
            if f <= 1:
                col = fill if edge is None or f <= 0.72 else edge
    return col + (255,)


def render(size, rounded=True, ss=4):
    rows = []
    for py in range(size):
        row = bytearray([0])  # PNG filter: none
        for px in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    r, g, b, a = colour_at((px + (sx + 0.5) / ss) / size, (py + (sy + 0.5) / ss) / size, rounded)
                    acc[0] += r * a
                    acc[1] += g * a
                    acc[2] += b * a
                    acc[3] += a
            n = ss * ss
            a = acc[3]
            row += bytes([acc[0] // a, acc[1] // a, acc[2] // a, a // n] if a else [0, 0, 0, 0])
        rows.append(bytes(row))
    return png(size, b"".join(rows))


def png(size, raw):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico(images):
    """images: [(size, png bytes)] -> .ico with PNG entries (Windows Vista and later)."""
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        s = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", s, s, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return head + entries + blobs


SHAPES = shapes()


def main():
    out = paths.ICONS
    os.makedirs(out, exist_ok=True)
    files = {"icon-192.png": render(192), "icon-512.png": render(512, ss=2),
             "icon-maskable-512.png": render(512, rounded=False, ss=2)}
    files["app.ico"] = ico([(s, render(s)) for s in (16, 24, 32, 48, 64, 256)])
    for name, data in files.items():
        with open(os.path.join(out, name), "wb") as f:
            f.write(data)
        print(f"{name}: {len(data) // 1024 or 1} KB")


if __name__ == "__main__":
    main()
