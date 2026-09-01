"""Deterministic Pillow-generated image fixtures. Writes to TWO directories.

1. The SC-1 quickstart corpus, in this same directory
   (docs/samples/images/): sprite_knight.png, chart_revenue.png,
   screenshot_settings.png. docs/samples/images-keyless.yaml globs `*.png`
   over this directory and .github/workflows/sc1-quickstart-clock.yml asserts
   the sprite ranks first for "pixel art". Adding ANY image here changes what
   that gate measures -- see tests/fixtures/images/README.md.

2. The adversarial image_meta fixtures, in tests/fixtures/images/: the
   swatch_* palette / tRNS / greyscale / RGBA / GIF / JPEG / WebP cases.
   These are test inputs, not sample content, so they deliberately do NOT
   live in the quickstart corpus.

Regenerate with (from the repo root):
    cd python && uv run --no-sync python ../docs/samples/images/generate_fixtures.py

No randomness — every draw call uses fixed coordinates/colors, so re-running
this script reproduces materially the same image (modulo the underlying
Pillow/zlib version, which can vary a few PNG bytes but never the pixels).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# The quickstart sample corpus (this directory).
OUT = Path(__file__).resolve().parent
# The adversarial image_meta fixtures. Repo root is three levels up from
# docs/samples/images/.
FIXTURES = OUT.parents[2] / "tests" / "fixtures" / "images"


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size
        )
    except Exception:
        return ImageFont.load_default()


# ---------------------------------------------------------------------------
# sprite_knight.png — blocky pixel-art game sprite: an 16x16 logical grid of
# big square "pixels" scaled up 8x, flat saturated palette, checkerboard
# transparency backdrop like a game-asset sheet preview.
# ---------------------------------------------------------------------------
def make_sprite_knight() -> Image.Image:
    grid = 16
    scale = 8
    size = grid * scale
    img = Image.new("RGB", (size, size), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    # checkerboard "transparency" backdrop, the classic sprite-preview look
    checker_a, checker_b = (222, 222, 222), (255, 255, 255)
    cell = scale
    for gy in range(grid):
        for gx in range(grid):
            color = checker_a if (gx + gy) % 2 == 0 else checker_b
            draw.rectangle(
                [gx * cell, gy * cell, (gx + 1) * cell - 1, (gy + 1) * cell - 1],
                fill=color,
            )

    # 16x16 pixel-art knight, palette-indexed by hand (0 = transparent/bg)
    SKY = None
    HELM = (90, 90, 110)      # steel helmet
    HELM_HI = (150, 150, 170)  # helmet highlight
    PLUME = (200, 30, 30)     # red plume
    SKIN = (230, 180, 140)
    ARMOR = (70, 90, 150)     # blue armor
    ARMOR_HI = (110, 140, 200)
    BELT = (120, 90, 40)
    BOOT = (50, 40, 35)
    SWORD = (200, 200, 210)
    HILT = (150, 110, 40)
    OUTLINE = (20, 20, 25)

    P = {
        None: None,
        ".": None,
        "K": OUTLINE,
        "h": HELM,
        "H": HELM_HI,
        "p": PLUME,
        "s": SKIN,
        "a": ARMOR,
        "A": ARMOR_HI,
        "b": BELT,
        "o": BOOT,
        "w": SWORD,
        "g": HILT,
    }

    # 16 rows x 16 cols pixel map (row-major, top to bottom)
    rows = [
        "................",
        "......pp........",
        ".....pKKp.......",
        "....KhhhhK......",
        "....KhHHhK.w....",
        "....KssssK.w....",
        "...KKssssKKw....",
        "..KaaAAaaK.w....",
        "..KaaAAaaKg.....",
        "..KKaaaaKK......",
        "...KbbbbK.......",
        "...KaaaaK.......",
        "...Ka..aK.......",
        "...Ka..aK.......",
        "..Koo..ooK......",
        "..KK....KK......",
    ]
    for gy, row in enumerate(rows):
        for gx, ch in enumerate(row):
            color = P.get(ch)
            if color is None:
                continue
            draw.rectangle(
                [gx * cell, gy * cell, (gx + 1) * cell - 1, (gy + 1) * cell - 1],
                fill=color,
            )
    return img


# ---------------------------------------------------------------------------
# chart_revenue.png — a line chart: white canvas, axes, gridlines, a rising
# trend line with data-point markers, tick marks, axis label bars.
# ---------------------------------------------------------------------------
def make_chart_revenue() -> Image.Image:
    W, H = 320, 220
    img = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    font = _font(12)
    small = _font(10)

    margin_l, margin_r, margin_t, margin_b = 44, 16, 30, 30
    plot_l, plot_t = margin_l, margin_t
    plot_r, plot_b = W - margin_r, H - margin_b

    draw.text((margin_l, 8), "Quarterly Revenue", fill=(20, 20, 20), font=font)

    # gridlines (horizontal), light gray
    for i in range(5):
        y = plot_t + i * (plot_b - plot_t) / 4
        draw.line([(plot_l, y), (plot_r, y)], fill=(225, 225, 225), width=1)

    # axes
    draw.line([(plot_l, plot_t), (plot_l, plot_b)], fill=(30, 30, 30), width=2)
    draw.line([(plot_l, plot_b), (plot_r, plot_b)], fill=(30, 30, 30), width=2)

    # x tick labels
    quarters = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"]
    values = [20, 35, 30, 55, 70, 90]  # rising trend, out of 100
    n = len(values)
    xs = [plot_l + i * (plot_r - plot_l) / (n - 1) for i in range(n)]
    ys = [plot_b - (v / 100.0) * (plot_b - plot_t) for v in values]

    for x, label in zip(xs, quarters):
        draw.text((x - 6, plot_b + 6), label, fill=(80, 80, 80), font=small)

    # y tick labels
    for i in range(5):
        val = i * 25
        y = plot_b - (val / 100.0) * (plot_b - plot_t)
        draw.text((6, y - 5), str(val), fill=(80, 80, 80), font=small)

    # the trend line itself, bold, plus point markers
    pts = list(zip(xs, ys))
    draw.line(pts, fill=(30, 110, 200), width=3, joint="curve")
    for x, y in pts:
        r = 3
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255), outline=(30, 110, 200), width=2)

    return img


# ---------------------------------------------------------------------------
# screenshot_settings.png — a desktop-app "Settings" screen mockup: title
# bar with window controls, left sidebar nav, main panel with labeled rows
# and toggle switches, a primary button.
# ---------------------------------------------------------------------------
def make_screenshot_settings() -> Image.Image:
    W, H = 360, 260
    img = Image.new("RGB", (W, H), (245, 246, 248))
    draw = ImageDraw.Draw(img)
    font = _font(13)
    small = _font(11)

    # title bar
    draw.rectangle([0, 0, W, 28], fill=(35, 38, 46))
    for i, color in enumerate([(230, 90, 80), (230, 190, 70), (90, 200, 100)]):
        cx = 14 + i * 16
        draw.ellipse([cx - 5, 14 - 5, cx + 5, 14 + 5], fill=color)
    draw.text((W / 2 - 30, 7), "Settings", fill=(230, 230, 230), font=font)

    # sidebar
    draw.rectangle([0, 28, 100, H], fill=(255, 255, 255))
    draw.line([(100, 28), (100, H)], fill=(220, 220, 220), width=1)
    nav_items = ["General", "Display", "Sound", "Network", "Privacy"]
    for i, label in enumerate(nav_items):
        y = 44 + i * 30
        active = i == 1
        if active:
            draw.rectangle([6, y - 6, 94, y + 18], fill=(225, 235, 250))
        draw.text((16, y), label, fill=(40, 40, 45) if active else (90, 90, 95), font=small)

    # main panel: rows with label + toggle switch
    rows = ["Dark mode", "Auto-brightness", "Show notifications", "Night shift"]
    states = [True, False, True, False]
    panel_x = 118
    for i, (label, on) in enumerate(zip(rows, states)):
        y = 50 + i * 42
        draw.text((panel_x, y), label, fill=(30, 30, 35), font=small)
        draw.line([(panel_x, y + 22), (W - 20, y + 22)], fill=(230, 230, 232), width=1)

        # toggle switch, top-right of row
        tw, th = 40, 20
        tx, ty = W - 20 - tw, y - 2
        track_color = (60, 140, 230) if on else (210, 210, 214)
        draw.rounded_rectangle([tx, ty, tx + tw, ty + th], radius=th // 2, fill=track_color)
        knob_x = tx + tw - th // 2 - 2 if on else tx + th // 2 - 2
        draw.ellipse([knob_x, ty + 2, knob_x + th - 4, ty + th - 2], fill=(255, 255, 255))

    # primary button, bottom-right
    bx0, by0, bx1, by1 = W - 120, H - 40, W - 20, H - 14
    draw.rounded_rectangle([bx0, by0, bx1, by1], radius=6, fill=(50, 120, 220))
    draw.text((bx0 + 24, by0 + 5), "Save", fill=(255, 255, 255), font=small)

    return img


# ---------------------------------------------------------------------------
# Task 9 Correction 1 fixtures — cover the PIL `mode`/`has_alpha` value
# space the `image_meta` deriver's rung-0 parity test measures Rust against.
# Deliberately tiny (8x8): these exist to pin metadata VALUES, not to look
# like real game art.
# ---------------------------------------------------------------------------
def make_palette_no_alpha() -> Image.Image:
    """A plain paletted (mode "P") PNG with no tRNS chunk."""
    img = Image.new("P", (8, 8))
    palette = [0, 0, 0] * 256
    palette[0:3] = [255, 0, 0]
    palette[3:6] = [0, 255, 0]
    img.putpalette(palette)
    px = img.load()
    for y in range(8):
        for x in range(8):
            px[x, y] = 0 if (x + y) % 2 == 0 else 1
    return img


def make_palette_with_trns() -> Image.Image:
    """A paletted (mode "P") PNG with an explicit tRNS chunk — the case
    Pillow's `has_alpha` catches via `"transparency" in img.info` even
    though `img.mode` is "P", not "RGBA"/"LA"/"PA"."""
    img = make_palette_no_alpha()
    img.info["transparency"] = 0  # palette index 0 is fully transparent
    return img


def make_grayscale_l() -> Image.Image:
    """Mode "L" (8-bit grayscale, no alpha)."""
    img = Image.new("L", (8, 8), 128)
    px = img.load()
    for y in range(8):
        for x in range(8):
            px[x, y] = (x * 32) % 256
    return img


def make_rgba() -> Image.Image:
    """Mode "RGBA" with real per-pixel alpha."""
    img = Image.new("RGBA", (8, 8), (200, 30, 30, 255))
    px = img.load()
    for y in range(4):
        for x in range(8):
            px[x, y] = (200, 30, 30, 0)  # top half fully transparent
    return img


def make_small_rgb() -> Image.Image:
    """Plain mode "RGB", no alpha, no palette — the baseline case."""
    img = Image.new("RGB", (8, 8), (30, 110, 200))
    return img


def main() -> None:
    make_sprite_knight().save(OUT / "sprite_knight.png", optimize=True)
    make_chart_revenue().save(OUT / "chart_revenue.png", optimize=True)
    make_screenshot_settings().save(OUT / "screenshot_settings.png", optimize=True)

    FIXTURES.mkdir(parents=True, exist_ok=True)
    make_palette_no_alpha().save(FIXTURES / "swatch_palette.png")
    make_palette_with_trns().save(FIXTURES / "swatch_palette_trns.png")
    make_grayscale_l().save(FIXTURES / "swatch_grayscale.png")
    make_rgba().save(FIXTURES / "swatch_rgba.png")
    rgb = make_small_rgb()
    rgb.save(FIXTURES / "swatch_rgb.jpg", quality=90)
    rgb.save(FIXTURES / "swatch_rgb.webp")
    rgb.convert("P", palette=Image.ADAPTIVE).save(FIXTURES / "swatch_rgb.gif")

    for d in (OUT, FIXTURES):
        print(f"-- {d}")
        for p in sorted(d.iterdir()):
            if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                print(" ", p.name, p.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
