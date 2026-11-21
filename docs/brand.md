# Inrface brand files

The name is **Inrface**. The wordmark styles it **inЯface**, with a reversed R. Write it as plain "Inrface" in text, so it can be searched and typed.

All files are built from `brand/source.jpg` by `scripts/make_brand.py`. To update them, replace the source (ideally with a larger or vector original) and run:

```
.venv/bin/python scripts/make_brand.py
```

## Files

Served by the app from `innerface/static/`:

| File | Use |
|---|---|
| `brand/logo.png` | Stacked logo: mark above the name, transparent background |
| `brand/logo-horizontal.png` | Mark beside the name: page headers, docs |
| `brand/logo-dark.png`, `brand/logo-horizontal-dark.png` | The same with a white name, for dark backgrounds |
| `brand/mark.png` | The mark alone, transparent, colours brightened so it reads on light and dark |
| `brand/mark-original.png` | The mark in the source's original colours |
| `brand/mark-simple.png` | Simplified mark (frame and diamond) for very small sizes |
| `brand/wordmark.png` | The name alone, navy, for light backgrounds |
| `brand/wordmark-white.png` | The name alone, white, for dark backgrounds |
| `brand/og-image.png` | 1200×630 link-preview image |
| `icons/icon-192.png`, `icons/icon-512.png` | App icons (home screen, notifications), on the app background `#f6f3ee` |
| `icons/icon-maskable-512.png` | Android adaptive icon, with the mark inside the safe zone |
| `icons/apple-touch-icon.png` | iPhone home screen, 180×180 |
| `favicon.ico`, `icons/favicon-32.png` | Browser tabs (16–64 px, simplified mark) |

## Colours

| | Hex | Where |
|---|---|---|
| Navy | `#114463` | Wordmark |
| Deep blue | `#1b3a6e` | Mark gradient, bottom left |
| Blue | `#2a80be` | Mark gradient |
| Teal | `#4eb296` | Mark gradient |
| Orange | `#f0963a` | Mark gradient, top right |

## Rules of thumb

- **Background:** the mark is transparent and brightened to sit directly on the page colour, light or dark. Don't put it on a white tile. With the name, use the navy wordmark on light backgrounds and the white one on dark.
- **Small sizes:** below about 48 px, use the simplified mark.
- **Clear space:** keep space around the logo of at least the height of the "i" in the wordmark.
