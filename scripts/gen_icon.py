"""Generate assets/icon.png (64x64) for the SpaceMouse WASD app.

Pure stdlib: renders a rounded dark tile with a gradient orbit ring and a
teal puck dot, supersampled for antialiasing, and writes a PNG by hand.
Run from the repo root:  python scripts/gen_icon.py
"""
import math
import os
import struct
import zlib

S = 64
C = S / 2.0
BG = (18, 21, 27)
TEAL = (56, 225, 200)
BLUE = (96, 165, 250)


def clamp01(v):
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def rounded_rect_sd(x, y):
    """Signed distance to a rounded square filling the tile (r=14)."""
    r, hw = 14.0, C - 1.0
    qx = abs(x - C) - (hw - r)
    qy = abs(y - C) - (hw - r)
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) - r


def sample(x, y):
    a_bg = clamp01(0.5 - rounded_rect_sd(x, y))
    if a_bg <= 0.0:
        return (0.0, 0.0, 0.0, 0.0)
    col = BG
    d = math.hypot(x - C, y - C)
    # orbit ring, gradient teal->blue around the circumference
    ring = clamp01(3.0 - abs(d - 20.5))
    if ring > 0.0:
        t = (math.atan2(y - C, x - C) / math.pi + 1.0) / 2.0
        col = mix(col, mix(TEAL, BLUE, t), ring)
    # center puck dot
    dot = clamp01(8.0 - d)
    if dot > 0.0:
        col = mix(col, TEAL, dot)
    return (col[0], col[1], col[2], 255.0 * a_bg)


def render():
    buf = bytearray()
    sub = (0.25, 0.75)
    for y in range(S):
        for x in range(S):
            r = g = b = a = 0.0
            for oy in sub:
                for ox in sub:
                    sr, sg, sb, sa = sample(x + ox, y + oy)
                    r += sr; g += sg; b += sb; a += sa
            buf += bytes((int(r / 4), int(g / 4), int(b / 4), int(a / 4)))
    return bytes(buf)


def write_png(path, width, height, rgba):
    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data +
                struct.pack('>I', zlib.crc32(tag + data)))
    raw = b''.join(b'\x00' + rgba[y * width * 4:(y + 1) * width * 4]
                   for y in range(height))
    png = (b'\x89PNG\r\n\x1a\n' +
           chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)) +
           chunk(b'IDAT', zlib.compress(raw, 9)) +
           chunk(b'IEND', b''))
    with open(path, 'wb') as f:
        f.write(png)


if __name__ == '__main__':
    out = os.path.join(os.path.dirname(__file__), '..', 'assets', 'icon.png')
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    write_png(out, S, S, render())
    print('wrote', out)
