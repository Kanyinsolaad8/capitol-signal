"""Draw the app icons (three rising bars on blue) as PNGs, with no extra libraries."""
import pathlib, struct, zlib

OUT = pathlib.Path(__file__).resolve().parent / "static"
BLUE, WHITE, PALE = (47, 74, 138), (255, 255, 255), (201, 214, 240)


def draw(size):
    px = [[BLUE] * size for _ in range(size)]
    bw, gap = size * 0.14, size * 0.07
    x0, base = size / 2 - (3 * bw + 2 * gap) / 2, size * 0.74
    for i, h in enumerate((0.26, 0.38, 0.52)):
        left = x0 + i * (bw + gap)
        for y in range(int(base - size * h), int(base)):
            for x in range(int(left), int(left + bw)):
                px[y][x] = WHITE
    for y in range(int(base + size * 0.035), int(base + size * 0.06)):
        for x in range(int(x0 - size * 0.04), int(x0 + 3 * bw + 2 * gap + size * 0.04)):
            px[y][x] = PALE
    return px


def png(px):
    h, w = len(px), len(px[0])
    raw = b"".join(b"\x00" + bytes(c for p in row for c in p) for row in px)
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for name, size in (("icon-512.png", 512), ("icon-192.png", 192), ("apple-touch-icon.png", 180)):
        (OUT / name).write_bytes(png(draw(size)))
    print("icons written")
