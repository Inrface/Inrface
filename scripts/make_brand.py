"""Build Inrface's logo files from brand/source.jpg (a flat logo on a white background).

    .venv/bin/python scripts/make_brand.py [path/to/source]

The source is split into the mark (the icon above) and the wordmark (the name below).
White is turned into transparency by "un-blending" each pixel from white, which keeps
anti-aliased edges smooth. Swap in a higher-resolution or vector original later and rerun.
"""
import sys
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "innerface" / "static"
NAVY = (17, 68, 99)
APP_BG = (246, 243, 238, 255)  # --bg in style.css


def ink_mask(im: Image.Image, threshold: int = 200) -> Image.Image:
    r, g, b = im.split()
    darkest = ImageChops.darker(ImageChops.darker(r, g), b)
    return darkest.point(lambda v: 255 if v < threshold else 0)


def unblend(im: Image.Image) -> Image.Image:
    """Return RGBA where the white background is transparent and edges keep their colour."""
    src = im.convert("RGB")
    out = Image.new("RGBA", src.size)
    px, po = src.load(), out.load()
    for y in range(src.height):
        for x in range(src.width):
            c = px[x, y]
            a = max(255 - v for v in c) / 255
            if a < 0.07:  # JPEG noise on the background
                po[x, y] = (0, 0, 0, 0)
                continue
            rgb = tuple(max(0, min(255, round(255 - (255 - v) / a))) for v in c)
            po[x, y] = (*rgb, round(a * 255))
    return out


def brighten(im: Image.Image, floor: float = 0.55, saturate: float = 1.15) -> Image.Image:
    """Lift the dark end of the colours so the mark reads on light and dark backgrounds alike."""
    import colorsys
    out = im.copy()
    px = out.load()
    for y in range(out.height):
        for x in range(out.width):
            r, g, b, a = px[x, y]
            if not a:
                continue
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            r2, g2, b2 = colorsys.hsv_to_rgb(h, min(1.0, s * saturate), floor + (1 - floor) * v)
            px[x, y] = (round(r2 * 255), round(g2 * 255), round(b2 * 255), a)
    return out


def trim(im: Image.Image) -> Image.Image:
    return im.crop(im.getchannel("A").getbbox())


def recolor(im: Image.Image, rgb) -> Image.Image:
    solid = Image.new("RGBA", im.size, (*rgb, 0))
    solid.putalpha(im.getchannel("A"))
    return solid


def fit(im: Image.Image, box: int) -> Image.Image:
    scale = box / max(im.size)
    return im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)


def on_canvas(im: Image.Image, size, fill_ratio: float, bg=(255, 255, 255, 255)) -> Image.Image:
    w, h = size if isinstance(size, tuple) else (size, size)
    canvas = Image.new("RGBA", (w, h), bg)
    art = fit(im, round(min(w, h) * fill_ratio))
    canvas.alpha_composite(art, ((w - art.width) // 2, (h - art.height) // 2))
    return canvas


GRADIENT = [(27, 58, 110), (42, 128, 190), (78, 178, 150), (240, 150, 58)]  # bottom-left to top-right, as in the logo


def gradient(size: int) -> Image.Image:
    g = Image.new("RGBA", (size, size))
    px = g.load()
    stops = len(GRADIENT) - 1
    for y in range(size):
        for x in range(size):
            t = (x + (size - 1 - y)) / (2 * (size - 1)) * stops
            i = min(int(t), stops - 1)
            f = t - i
            a, b = GRADIENT[i], GRADIENT[i + 1]
            px[x, y] = (*(round(a[k] + (b[k] - a[k]) * f) for k in range(3)), 255)
    return g


def simple_mark(size: int) -> Image.Image:
    """The logo reduced to its frame and centre diamond, for favicons."""
    from PIL import ImageDraw
    s = size / 512
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([34 * s, 34 * s, 478 * s, 478 * s], radius=88 * s, outline=255, width=round(46 * s))
    cx, cy, r = 256 * s, 262 * s, 92 * s
    d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], outline=255, width=round(40 * s))
    out = gradient(size)
    out.putalpha(mask)
    return out


def main(source: Path) -> None:
    im = Image.open(source).convert("RGB")
    ink = ink_mask(im)
    full = ink.getbbox()
    # The first fully empty row below the top of the art separates the mark from the name.
    gap = next(y for y in range(full[1] + 10, full[3]) if ink.crop((0, y, im.width, y + 1)).getbbox() is None)
    pad = 4
    # Crop each part to its own ink, so faint background marks between them are left out.
    mb = ink.crop((0, 0, im.width, gap)).getbbox()
    wb = ink.crop((0, gap, im.width, im.height)).getbbox()
    mark = trim(unblend(im.crop((mb[0] - pad, mb[1] - pad, mb[2] + pad, mb[3] + pad))))
    word = trim(unblend(im.crop((wb[0] - pad, gap + wb[1] - pad, wb[2] + pad, gap + wb[3] + pad))))
    word = recolor(word, NAVY)  # one flat colour: removes JPEG tint in the letters
    mark.save(OUT / "brand" / "mark-original.png") if (OUT / "brand").exists() else None
    mark = brighten(mark)

    brand, icons = OUT / "brand", OUT / "icons"
    brand.mkdir(exist_ok=True)
    icons.mkdir(exist_ok=True)

    mark.save(brand / "mark.png")
    word.save(brand / "wordmark.png")
    recolor(word, (255, 255, 255)).save(brand / "wordmark-white.png")

    # Stacked logo, as in the source, and a horizontal one for headers.
    # "-dark" versions have a white name, for dark backgrounds.
    for suffix, name in (("", word), ("-dark", recolor(word, (255, 255, 255)))):
        gap_px = round(mark.height * 0.12)
        stacked = Image.new("RGBA", (max(mark.width, name.width), mark.height + gap_px + name.height), (0, 0, 0, 0))
        stacked.alpha_composite(mark, ((stacked.width - mark.width) // 2, 0))
        stacked.alpha_composite(name, ((stacked.width - name.width) // 2, mark.height + gap_px))
        stacked.save(brand / f"logo{suffix}.png")
        h2 = round(mark.height * 0.3)  # name height relative to the mark
        w2 = name.resize((round(name.width * h2 / name.height), h2), Image.LANCZOS)
        horiz = Image.new("RGBA", (mark.width + round(mark.width * 0.18) + w2.width, mark.height), (0, 0, 0, 0))
        horiz.alpha_composite(mark, (0, 0))
        horiz.alpha_composite(w2, (mark.width + round(mark.width * 0.18), (mark.height - w2.height) // 2))
        horiz.save(brand / f"logo-horizontal{suffix}.png")
    stacked = Image.open(brand / "logo.png")

    # App icons sit on the app's own background colour (home screens need an opaque tile).
    on_canvas(mark, 512, 0.84, bg=APP_BG).save(icons / "icon-512.png")
    on_canvas(mark, 192, 0.84, bg=APP_BG).save(icons / "icon-192.png")
    on_canvas(mark, 512, 0.64, bg=APP_BG).save(icons / "icon-maskable-512.png")  # inside the 80% safe zone
    on_canvas(mark, 180, 0.82, bg=APP_BG).convert("RGB").save(icons / "apple-touch-icon.png")
    # Browser tabs: the full mark is unreadable at 16-32 px, so use a simplified one.
    small = brighten(simple_mark(512))
    small.resize((32, 32), Image.LANCZOS).save(icons / "favicon-32.png")
    small.save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    small.save(brand / "mark-simple.png")
    # Link previews (Open Graph): 1200x630.
    og = Image.new("RGBA", (1200, 630), APP_BG)
    og.alpha_composite(on_canvas(trim(stacked), (1200, 630), 0.78, bg=(0, 0, 0, 0)))
    og.convert("RGB").save(brand / "og-image.png")
    print("mark", mark.size, "wordmark", word.size)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "brand" / "source.jpg")
