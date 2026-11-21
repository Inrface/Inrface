"""Build Inrface's logo files from brand/source.jpg (the logo sheet: full logo on the left, app icon on the right,
flat gold and cream on a dark green background).

    .venv/bin/python scripts/make_brand.py [path/to/source]

The full logo (the mark above the name) is cut out of the sheet and its green background removed, one flat colour at a
time, so the edges stay clean. The icons are built from the mark, not cut from the small icon on the sheet, which is
too small to enlarge. Swap in a larger or vector original later and rerun; if the sheet's layout changes, the
windows below are the only things to adjust.
"""
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "innerface" / "static"
GITHUB = ROOT / "brand"

# The colours on the sheet, which is what the cut-out is keyed on.
SHEET_GREEN, SHEET_GOLD, SHEET_CREAM = (38, 72, 47), (219, 153, 4), (252, 232, 160)
# The colours chosen for the brand, which is what gets written.
GREEN = (0x28, 0x4a, 0x31)   # #284a31: icon tile, "Face" on light backgrounds
GOLD = (0xe8, 0xa3, 0x02)    # #e8a302: the mark, "inr"
CREAM = (252, 232, 160)
APP_BG = GREEN

# Where things sit on the sheet: (left, top, right, bottom). Each is cropped to its own ink afterwards.
MARK_WINDOW = (110, 170, 500, 470)
WORD_WINDOW = (90, 472, 520, 590)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def cut_out(im: Image.Image, window, fill_lines: bool = False) -> Image.Image:
    """The art inside the window as RGBA with the green removed. Each pixel is read as a mix of the background and
    gold or cream (whichever fits better), which gives a clean edge and flat colours. Dark lines drawn inside the
    art are kept as they are."""
    src = im.convert("RGB").crop(window)
    w, h = src.size
    px = src.load()
    out = Image.new("RGBA", (w, h))
    po = out.load()
    g = tuple(a - b for a, b in zip(SHEET_GOLD, SHEET_GREEN))
    k = tuple(a - b for a, b in zip(SHEET_CREAM, SHEET_GREEN))
    soft = Image.new("L", (w, h))
    ps = soft.load()
    choice = {}
    for y in range(h):
        for x in range(w):
            d = tuple(a - b for a, b in zip(px[x, y], SHEET_GREEN))
            best = None
            for name, v, col in (("gold", g, GOLD), ("cream", k, CREAM)):
                alpha = max(0.0, min(1.0, _dot(d, v) / _dot(v, v)))
                miss = sum((d[i] - alpha * v[i]) ** 2 for i in range(3)) ** 0.5
                if best is None or miss < best[0]:
                    best = (miss, alpha, col)
            _, alpha, col = best
            alpha = 0.0 if alpha < 0.08 else alpha
            choice[x, y] = col
            ps[x, y] = round(alpha * 255)
            po[x, y] = (*col, round(alpha * 255))
    if not fill_lines:
        return out
    # Dark lines inside the art are close to the green, so they'd read as see-through. Fill what the art encloses.
    # (Only for the mark: letters have real holes, such as the inside of an a.)
    solid = soft.point(lambda v: 255 if v > 38 else 0)
    flood = solid.copy()
    ImageDraw.floodfill(flood, (0, 0), 128)   # the outside
    inside = flood.point(lambda v: 255 if v != 128 else 0)
    inside = inside.filter(ImageFilter.MinFilter(5))   # keep the real edge anti-aliased
    for y in range(h):
        for x in range(w):
            if inside.getpixel((x, y)) and ps[x, y] < 250:
                po[x, y] = (*px[x, y], 255)
    return out


def trim(im: Image.Image, pad: int = 0) -> Image.Image:
    box = im.getchannel("A").point(lambda v: 255 if v > 20 else 0).getbbox()
    im = im.crop(box)
    if pad:
        bigger = Image.new("RGBA", (im.width + 2 * pad, im.height + 2 * pad), (0, 0, 0, 0))
        bigger.alpha_composite(im, (pad, pad))
        im = bigger
    return im


def swap_cream(im: Image.Image, rgb) -> Image.Image:
    """Recolour the cream letters, leaving gold and everything else alone."""
    out = im.copy()
    px = out.load()
    for y in range(out.height):
        for x in range(out.width):
            r, g, b, a = px[x, y]
            if a and (r, g, b) == CREAM:
                px[x, y] = (*rgb, a)
    return out


def silhouette(mark: Image.Image, rgb=GOLD) -> Image.Image:
    solid = Image.new("RGBA", mark.size, (*rgb, 0))
    solid.putalpha(mark.getchannel("A").point(lambda v: 255 if v > 20 else 0).filter(ImageFilter.GaussianBlur(0.6)))
    return solid


def fit(im: Image.Image, box: int) -> Image.Image:
    scale = box / max(im.size)
    return im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)


def on_canvas(im: Image.Image, size, fill_ratio: float, bg=(*GREEN, 255), radius: float = 0.0) -> Image.Image:
    w, h = size if isinstance(size, tuple) else (size, size)
    canvas = Image.new("RGBA", (w, h), bg)
    art = fit(im, round(min(w, h) * fill_ratio))
    canvas.alpha_composite(art, ((w - art.width) // 2, (h - art.height) // 2))
    if radius:
        mask = Image.new("L", (w * 4, h * 4), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, w * 4 - 1, h * 4 - 1], radius=radius * min(w, h) * 4, fill=255)
        canvas.putalpha(mask.resize((w, h), Image.LANCZOS))
    return canvas


def stack(mark: Image.Image, name: Image.Image) -> Image.Image:
    gap = round(mark.height * 0.1)
    width = max(mark.width, name.width)
    s = Image.new("RGBA", (width, mark.height + gap + name.height), (0, 0, 0, 0))
    s.alpha_composite(mark, ((width - mark.width) // 2, 0))
    s.alpha_composite(name, ((width - name.width) // 2, mark.height + gap))
    return s


def beside(mark: Image.Image, name: Image.Image) -> Image.Image:
    h2 = round(mark.height * 0.34)
    w2 = name.resize((round(name.width * h2 / name.height), h2), Image.LANCZOS)
    gap = round(mark.width * 0.16)
    s = Image.new("RGBA", (mark.width + gap + w2.width, mark.height), (0, 0, 0, 0))
    s.alpha_composite(mark, (0, 0))
    s.alpha_composite(w2, (mark.width + gap, (mark.height - w2.height) // 2))
    return s


def main(source: Path) -> None:
    im = Image.open(source).convert("RGB")
    mark = trim(cut_out(im, MARK_WINDOW, fill_lines=True), 2)
    word_dark = trim(cut_out(im, WORD_WINDOW), 2)             # for dark backgrounds: gold and cream, as on the sheet
    word_light = swap_cream(word_dark, GREEN)                 # for light backgrounds: the cream letters go green

    brand, icons = OUT / "brand", OUT / "icons"
    brand.mkdir(exist_ok=True)
    icons.mkdir(exist_ok=True)
    GITHUB.mkdir(exist_ok=True)
    (brand / "mark-original.png").unlink(missing_ok=True)

    mark.save(brand / "mark.png")
    word_light.save(brand / "wordmark.png")
    word_dark.save(brand / "wordmark-white.png")   # the name for dark backgrounds
    stack(mark, word_light).save(brand / "logo.png")
    stack(mark, word_dark).save(brand / "logo-dark.png")
    beside(mark, word_light).save(brand / "logo-horizontal.png")
    beside(mark, word_dark).save(brand / "logo-horizontal-dark.png")

    # App icons: the mark on the brand green (home screens need an opaque tile).
    on_canvas(mark, 512, 0.78).save(icons / "icon-512.png")
    on_canvas(mark, 192, 0.78).save(icons / "icon-192.png")
    on_canvas(mark, 512, 0.58).save(icons / "icon-maskable-512.png")   # inside the 80% safe zone
    on_canvas(mark, 180, 0.76).convert("RGB").save(icons / "apple-touch-icon.png")
    # Browser tabs: the thin dark lines would vanish at 16-32 px, so the mark is shown as one gold shape.
    shape = silhouette(mark)
    shape.save(brand / "mark-simple.png")
    tab = on_canvas(shape, 512, 0.8, radius=0.22)
    tab.resize((32, 32), Image.LANCZOS).save(icons / "favicon-32.png")
    tab.save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])

    # Link previews: the site and the app (1200x630), and the GitHub repository (1280x640).
    for path, size, ratio in ((brand / "og-image.png", (1200, 630), 0.7), (GITHUB / "github-social.png", (1280, 640), 0.7)):
        on_canvas(stack(mark, word_dark), size, ratio).convert("RGB").save(path)
    on_canvas(mark, 500, 0.78).convert("RGB").save(GITHUB / "github-avatar.png")   # for the organisation's picture
    print("mark", mark.size, "wordmark", word_dark.size)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "brand" / "source.jpg")
