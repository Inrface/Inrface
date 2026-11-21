# Inrface brand files

The name is **Inrface**. The wordmark styles it **inrFace**. Write it as plain "Inrface" in text, so it can be searched and typed.

All files are built from `brand/source.jpg` by `scripts/make_brand.py`. To update them, replace the source (ideally with a larger or vector original; the app icon on the sheet is too small to enlarge, so icons are built from the mark) and run:

```
.venv/bin/python scripts/make_brand.py
```

## Files

Served by the app from `innerface/static/`:

| File | Use |
|---|---|
| `brand/logo.png` | Stacked logo: mark above the name, transparent, for light backgrounds |
| `brand/logo-dark.png` | The same for dark backgrounds (the name in gold and cream) |
| `brand/logo-horizontal.png`, `brand/logo-horizontal-dark.png` | Mark beside the name: page headers, docs |
| `brand/mark.png` | The mark alone, transparent, reads on light and dark |
| `brand/mark-simple.png` | The mark as one gold shape, without its thin lines, for very small sizes |
| `brand/wordmark.png` | The name alone, for light backgrounds (gold "inr", green "Face") |
| `brand/wordmark-white.png` | The name alone, for dark backgrounds (gold "inr", cream "Face") |
| `brand/og-image.png` | 1200×630 link-preview image |
| `icons/icon-192.png`, `icons/icon-512.png` | App icons (home screen, notifications): the mark on the brand green |
| `icons/icon-maskable-512.png` | Android adaptive icon, with the mark inside the safe zone |
| `icons/apple-touch-icon.png` | iPhone home screen, 180×180 |
| `favicon.ico`, `icons/favicon-32.png` | Browser tabs (16–64 px, simplified mark on a green tile) |

In `brand/` at the top of the repository, for GitHub:

| File | Use |
|---|---|
| `github-social.png` | 1280×640. Upload under the repository's **Settings → General → Social preview** (GitHub has no way to set this from outside) |
| `github-avatar.png` | 500×500. For the organisation's picture, under **Settings → Profile** |

## Colours

| | Hex | Where |
|---|---|---|
| Gold | `#e8a302` | The mark, and "inr" in the name |
| Cream | `#fce8a0` | "Face" on dark backgrounds |
| Green | `#284a31` | Icon tile, "Face" on light backgrounds |

## Rules of thumb

- **Background:** the mark is transparent and sits directly on the page colour, light or dark. Don't put it on a white tile. With the name, use the files without `-dark` on light backgrounds and the `-dark` ones on dark.
- **Small sizes:** below about 48 px, use the simplified mark.
- **Clear space:** keep space around the logo of at least the height of the "i" in the wordmark.

The app uses the same two as its accent: green (`#284a31`) in light mode and gold (`#e8a302`) in dark mode, set once in `style.css` (and the three small stylesheets beside it).
