"""Generate the two decorative icons used on page 2 (wing / headband)."""

from __future__ import annotations

import math
import pathlib

from PIL import Image, ImageDraw

HERE = pathlib.Path(__file__).resolve().parent
ASSETS = HERE / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)

S = 4
W, H = 600 * S, 420 * S


def wing() -> None:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    base = (110 * S, 350 * S)
    tips = [(500, 80), (455, 205), (390, 265), (320, 305), (245, 335)]
    shades = [(109, 74, 255), (124, 92, 255), (143, 111, 255), (160, 132, 255), (180, 156, 255)]
    for i, (tx, ty) in enumerate(tips):
        tip = (tx * S, ty * S)
        ang = math.atan2(tip[1] - base[1], tip[0] - base[0])
        nx, ny = -math.sin(ang), math.cos(ang)
        p1 = (base[0] + nx * 26 * S, base[1] + ny * 26 * S)
        p2 = (base[0] - nx * 26 * S, base[1] - ny * 26 * S)
        t1 = (tip[0] + nx * 5 * S, tip[1] + ny * 5 * S)
        t2 = (tip[0] - nx * 5 * S, tip[1] - ny * 5 * S)
        d.polygon([p1, t1, tip, t2, p2], fill=shades[i] + (255,))
    # leading edge / body
    d.polygon([(80 * S, 360 * S), (135 * S, 245 * S), (220 * S, 165 * S),
               (320 * S, 110 * S), (430 * S, 80 * S), (500 * S, 80 * S),
               (420 * S, 150 * S), (330 * S, 210 * S), (240 * S, 265 * S),
               (160 * S, 320 * S)], fill=(90, 58, 230, 255))
    d.ellipse([base[0] - 30 * S, base[1] - 30 * S, base[0] + 30 * S, base[1] + 30 * S],
              fill=(70, 40, 200, 255))
    img = img.resize((600, 420), Image.LANCZOS)
    img.save(ASSETS / "wing.png")


def headband() -> None:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([100 * S, 140 * S, 500 * S, 300 * S], outline=(194, 118, 10, 255), width=40 * S)
    d.arc([110 * S, 150 * S, 490 * S, 290 * S], start=200, end=340,
          fill=(245, 190, 80, 255), width=10 * S)
    for cx, cy, r in [(180, 150, 20), (300, 138, 24), (420, 150, 20)]:
        d.ellipse([(cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S],
                  fill=(245, 190, 80, 255), outline=(160, 92, 4, 255), width=4 * S)
    d.ellipse([285 * S, 120 * S, 315 * S, 150 * S], fill=(160, 92, 4, 255))
    img = img.resize((600, 420), Image.LANCZOS)
    img.save(ASSETS / "headband.png")


wing()
headband()
print("icons written:", ASSETS / "wing.png", ASSETS / "headband.png")
