#!/usr/bin/env python3
"""
bookbuilder - turn a folder of project pages into a "from idea to finish" book.

Folder layout (inside your project folder):

    my_project/
      cover/            <- first page (also holds book settings)
        page.md
        cover.jpg
      1/                <- page 1   (names like "1", "02", "03 prototype" all work)
        page.md
        sketch.jpg
      2/
        page.md
        photo1.jpg
        photo2.jpg
      ...
      back/             <- last page
        page.md
        portrait.jpg

Each page.md may start with a front-matter block:

    ---
    title: First prototype
    date: 2025-03-14        (YYYY, YYYY-MM or YYYY-MM-DD)
    phase: Prototype
    layout: auto            (auto | text | split | grid | full)
    caption: Cardboard mock-up, version 2
    ---
    Markdown text for the page...

Commands:
    python bookbuilder.py init    my_project          # create an example project
    python bookbuilder.py preview my_project --watch  # live HTML preview in browser
    python bookbuilder.py pdf     my_project          # build the final PDF
    python bookbuilder.py site    my_project          # one-page website (index.html)
    python bookbuilder.py build   my_project          # website + book preview + PDF
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import http.server
import json
import os
import re
import shutil
import socketserver
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

try:
    import markdown as md_lib
except ImportError:  # pragma: no cover
    md_lib = None

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover
    Image = None

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff"}
TEXT_NAMES = ("page.md", "text.md", "index.md", "page.txt", "text.txt")
COVER_NAMES = {"cover", "main", "front", "naslovnica", "00_cover", "0_cover"}
BACK_NAMES = {"back", "last", "end", "zadnja", "back_cover", "99_back"}

PAGE_SIZES = {  # width, height in mm
    "a4-landscape": (297, 210),
    "a4": (210, 297),
    "a5-landscape": (210, 148),
    "square": (210, 210),
    "letter-landscape": (279.4, 215.9),
    "letter": (215.9, 279.4),
}

I18N = {
    "en": {"months": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
           "day": "{d} {m} {y}", "month": "{m} {y}", "journey": "The journey", "to": " to ",
           "page": "Page", "untitled": "Untitled project", "pages": "pages",
           "toggle": "Toggle overview", "print": "Print / save as PDF",
           "site": "Website", "book": "The book", "pdf": "PDF",
           "gallery": "Open gallery", "photos": "photos", "close": "Close", "prev": "Previous photo",
           "next": "Next photo"},
    "sl": {"months": ["januar", "februar", "marec", "april", "maj", "junij", "julij", "avgust",
                      "september", "oktober", "november", "december"],
           "day": "{d}. {mn}. {y}", "month": "{m} {y}", "journey": "Pot projekta", "to": " – ",
           "page": "Stran", "untitled": "Projekt brez naslova", "pages": "strani",
           "toggle": "Pregled strani", "print": "Natisni / shrani PDF",
           "site": "Spletna stran", "book": "Knjiga", "pdf": "PDF",
           "gallery": "Odpri galerijo", "photos": "fotografij", "close": "Zapri", "prev": "Prejšnja fotografija",
           "next": "Naslednja fotografija"},
}
T = I18N["en"]


def set_lang(lang: str):
    global T
    T = I18N.get((lang or "en").lower()[:2], I18N["en"])


BOOK_MAX_IMAGES = 4   # photos per book page; the website gallery shows all of them

PHASE_COLORS = ["#13315C", "#9A6B4F", "#3F6470", "#B7832F", "#5F6B3A", "#7A4E6E", "#DC0526"]


# --------------------------------------------------------------------------- model

@dataclass
class Page:
    kind: str                      # "cover" | "page" | "back"
    folder: Path
    number: int | None = None
    meta: dict = field(default_factory=dict)
    body_html: str = ""
    images: list[Path] = field(default_factory=list)
    date: dt.date | None = None
    date_label: str = ""
    date_grain: str = ""

    @property
    def title(self) -> str:
        return self.meta.get("title") or ("" if self.kind != "page" else f"{T['page']} {self.number}")

    @property
    def phase(self) -> str:
        return self.meta.get("phase", "")


@dataclass
class Book:
    root: Path
    cover: Page | None
    pages: list[Page]
    back: Page | None
    settings: dict

    @property
    def all_pages(self) -> list[Page]:
        return [p for p in [self.cover, *self.pages, self.back] if p]


# --------------------------------------------------------------------------- parsing

FRONT_RE = re.compile(r"^\s*---\s*\n(.*?)\n---\s*\n?", re.S)


def parse_front_matter(text: str) -> tuple[dict, str]:
    """Tiny 'key: value' front-matter parser (no YAML dependency)."""
    m = FRONT_RE.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.strip().startswith("#"):
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip().strip('"').strip("'")
    return meta, text[m.end():]


def parse_date(value: str) -> tuple[dt.date | None, str]:
    """Returns (date, granularity) where granularity is d|m|y, or (None, original text)."""
    value = (value or "").strip()
    if not value:
        return None, ""
    for fmt, grain in (("%Y-%m-%d", "d"), ("%Y-%m", "m"), ("%Y", "y"), ("%d.%m.%Y", "d")):
        try:
            return dt.datetime.strptime(value, fmt).date(), grain
        except ValueError:
            continue
    return None, value  # free text like "Summer 2024" is shown but not placed on the rail


def format_date(d: dt.date, grain: str) -> str:
    m = T["months"][d.month - 1]
    if grain == "d":
        return T["day"].format(d=d.day, m=m, mn=d.month, y=d.year)
    if grain == "m":
        return T["month"].format(m=m, y=d.year)
    return str(d.year)


def render_markdown(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    if md_lib:
        return md_lib.markdown(text, extensions=["extra", "sane_lists"])
    return "".join(f"<p>{html.escape(p)}</p>" for p in text.split("\n\n"))


def leading_number(name: str) -> int | None:
    m = re.match(r"^\s*(\d+)", name)
    return int(m.group(1)) if m else None


def load_page(folder: Path, kind: str, number: int | None = None) -> Page:
    page = Page(kind=kind, folder=folder, number=number)
    text_file = next((folder / n for n in TEXT_NAMES if (folder / n).exists()), None)
    if text_file is None:
        text_file = next(iter(sorted(folder.glob("*.md")) + sorted(folder.glob("*.txt"))), None)
    if text_file:
        meta, body = parse_front_matter(text_file.read_text(encoding="utf-8"))
        page.meta = meta
        page.body_html = render_markdown(body)
    page.images = sorted(
        (f for f in folder.iterdir() if f.suffix.lower() in IMAGE_EXT and not f.name.startswith(".")),
        key=lambda f: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", f.name)],
    )
    page.date, page.date_grain = parse_date(page.meta.get("date", ""))
    if page.date is None:
        page.date_label, page.date_grain = page.date_grain, ""
    return page


def load_book(root: Path) -> Book:
    if not root.is_dir():
        sys.exit(f"Folder not found: {root}")
    cover = back = None
    numbered: list[tuple[int, Page]] = []
    skipped = []
    for sub in sorted(root.iterdir()):
        if not sub.is_dir() or sub.name.startswith((".", "_")):
            continue
        low = sub.name.lower()
        if low in COVER_NAMES:
            cover = load_page(sub, "cover")
        elif low in BACK_NAMES:
            back = load_page(sub, "back")
        elif (n := leading_number(sub.name)) is not None:
            numbered.append((n, load_page(sub, "page", n)))
        else:
            skipped.append(sub.name)
    numbered.sort(key=lambda t: t[0])
    pages = [p for _, p in numbered]
    for i, p in enumerate(pages, 1):
        p.number = i
    if skipped:
        print(f"  note: ignored folders without a number: {', '.join(skipped)}")
    if cover is None:
        print("  note: no 'cover' folder found - the book will start with page 1")
    if back is None:
        print("  note: no 'back' folder found - the book will end with the last page")
    settings = dict(cover.meta) if cover else {}
    set_lang(settings.get("lang", "en"))
    for p in [cover, *pages, back]:
        if p and p.date:
            p.date_label = format_date(p.date, p.date_grain)
    return Book(root=root, cover=cover, pages=pages, back=back, settings=settings)


# --------------------------------------------------------------------------- images

def prepare_images(book: Book, out_dir: Path, max_px: int) -> dict[Path, str]:
    """Copy (and downscale / auto-rotate) images into out_dir/_img. Returns src -> relative url."""
    img_dir = out_dir / "_img"
    img_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[Path, str] = {}
    for page in book.all_pages:
        for src in page.images:
            stat = src.stat()
            key = hashlib.md5(f"{src.resolve()}|{stat.st_mtime}|{stat.st_size}|{max_px}".encode()).hexdigest()[:12]
            suffix = ".png" if src.suffix.lower() in {".png", ".gif"} else ".jpg"
            dst = img_dir / f"{key}{suffix}"
            if not dst.exists():
                if Image is None:
                    shutil.copy2(src, dst.with_suffix(src.suffix.lower()))
                    dst = dst.with_suffix(src.suffix.lower())
                else:
                    with Image.open(src) as im:
                        im = ImageOps.exif_transpose(im)
                        im.thumbnail((max_px, max_px))
                        if suffix == ".jpg":
                            im.convert("RGB").save(dst, "JPEG", quality=88, optimize=True)
                        else:
                            im.save(dst, "PNG", optimize=True)
            mapping[src] = f"_img/{dst.name}"
    return mapping


# --------------------------------------------------------------------------- timeline

def phase_colors(book: Book) -> dict[str, str]:
    colors, order = {}, []
    for p in book.pages:
        if p.phase and p.phase not in colors:
            palette = [c.strip() for c in book.settings.get("palette", "").split(",") if c.strip()] or PHASE_COLORS
            colors[p.phase] = palette[len(order) % len(palette)]
            order.append(p.phase)
    return colors


def rail_positions(pages: list[Page], mode: str = "dates") -> list[float]:
    """Position (0..1) of every page on the time rail. Uses dates when available,
    falls back to even spacing and interpolates pages without dates."""
    n = len(pages)
    if n == 0:
        return []
    if n == 1:
        return [0.5]
    dated = [(i, p.date) for i, p in enumerate(pages) if p.date]
    if mode == "even" or len(dated) < 2 or dated[0][1] == dated[-1][1]:
        return [i / (n - 1) for i in range(n)]
    start, end = dated[0][1], dated[-1][1]
    span = (end - start).days or 1
    pos: list[float | None] = [None] * n
    for i, d in dated:
        pos[i] = min(max((d - start).days / span, 0.0), 1.0)
    # fill undated pages by interpolating between known neighbours
    for i in range(n):
        if pos[i] is None:
            prev = next(((j, pos[j]) for j in range(i - 1, -1, -1) if pos[j] is not None), (i, 0.0))
            nxt = next(((j, pos[j]) for j in range(i + 1, n) if pos[j] is not None), (i, 1.0))
            if nxt[0] == prev[0]:
                pos[i] = prev[1]
            else:
                pos[i] = prev[1] + (nxt[1] - prev[1]) * (i - prev[0]) / (nxt[0] - prev[0])
    return [float(p) for p in pos]


def rail_html(book: Book, current: Page | None, positions: list[float], colors: dict[str, str]) -> str:
    dots = []
    for p, x in zip(book.pages, positions):
        cls = "dot now" if p is current else ("dot past" if current and p.number < current.number else "dot")
        color = colors.get(p.phase, "var(--dark)")
        dots.append(f'<span class="{cls}" style="left:{x * 100:.2f}%;--c:{color}"></span>')
    first = next((p.date_label for p in book.pages if p.date), "")
    last = next((p.date_label for p in reversed(book.pages) if p.date), "")
    first = book.settings.get("rail_start", first)
    last = book.settings.get("rail_end", last)
    progress = ""
    label = ""
    if current is not None:
        i = book.pages.index(current)
        x = positions[i]
        progress = f'<span class="fill" style="width:{x * 100:.2f}%"></span>'
        anchor = "start" if x < 0.15 else ("end" if x > 0.85 else "mid")
        text = " · ".join(t for t in (html.escape(current.date_label), html.escape(current.phase)) if t)
        if text:
            label = f'<span class="here {anchor}" style="left:{x * 100:.2f}%">{text}</span>'
    return f"""
    <div class="rail">
      <span class="end-label start">{html.escape(first)}</span>
      <div class="track">{progress}{''.join(dots)}{label}</div>
      <span class="end-label end">{html.escape(last)}</span>
    </div>"""


# --------------------------------------------------------------------------- theme
# Visual language: Inter, white paper, sand-coloured L-shaped brackets and a
# dotted timeline, navy headings and navy-framed captions.

def shade(color: str, f: float) -> str:
    """Darken a #RRGGBB colour by factor f (0..1). Returns the input if it can't be parsed."""
    h = color.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return color
    return "#{:02X}{:02X}{:02X}".format(*(round(c * f) for c in (r, g, b)))


def theme(book: Book) -> dict[str, str]:
    s = book.settings
    line = s.get("line", "#D3B59F")
    font = s.get("font", "")
    return {
        "line": line,                         # brackets, dotted timeline, number boxes
        "line_ink": shade(line, 0.62),        # the same tone, dark enough for small text
        "dark": s.get("dark", "#13315C"),     # headings, caption frames, closing block
        "accent": s.get("accent", "#DC0526"),
        "sans": (f"'{font}', " if font else "") + "'Inter', 'Segoe UI', 'Helvetica Neue', Arial, sans-serif",
    }


# Inter ships with the tool (fonts/ next to this script) and is copied into every output folder,
# so the preview, the website and the PDF look the same with or without internet.
FONTS_DIR = Path(__file__).resolve().parent / "fonts"
FONTS_LINK = '<link href="fonts/fonts.css" rel="stylesheet">'


def copy_fonts(out_dir: Path):
    if not (FONTS_DIR / "fonts.css").exists():
        print(f"  warning: {FONTS_DIR} is missing - falling back to system fonts")
        return
    dst = out_dir / "fonts"
    dst.mkdir(exist_ok=True)
    for f in FONTS_DIR.iterdir():
        if f.is_file() and f.suffix in {".css", ".woff2", ".txt"}:
            target = dst / f.name
            if not target.exists() or target.stat().st_size != f.stat().st_size:
                shutil.copy2(f, target)


def meta_line(page: Page) -> str:
    """'date · phase' with the phase in its own span (coloured by CSS)."""
    date = html.escape(page.date_label)
    phase = f'<span class="phase">{html.escape(page.phase)}</span>' if page.phase else ""
    return " · ".join(t for t in (date, phase) if t)


# --------------------------------------------------------------------------- book html

def figure(url: str, cls: str = "") -> str:
    return f'<figure class="{cls}"><img src="{html.escape(url)}" alt=""></figure>'


def choose_layout(page: Page) -> str:
    layout = (page.meta.get("layout") or "auto").lower()
    if layout != "auto":
        return layout
    n = len(page.images)
    if n == 0:
        return "text"
    if n == 1 and not page.body_html:
        return "full"
    if n <= 2:
        return "split"
    return "grid"


def cover_span(book: Book) -> str:
    return book.settings.get("span") or T["to"].join(
        x for x in (next((p.date_label for p in book.pages if p.date), ""),
                    next((p.date_label for p in reversed(book.pages) if p.date), "")) if x)


def render_cover(page: Page, img: dict[Path, str], book: Book) -> str:
    s = book.settings
    bg = figure(img[page.images[0]], "cover-img") if page.images else ""
    span = cover_span(book)
    return f"""
  <section class="sheet cover {'has-img' if bg else 'no-img'}">
    {bg}
    <div class="cover-text">
      {f'<p class="kicker">{html.escape(span)}</p>' if span else ''}
      <h1>{html.escape(s.get('title', T['untitled']))}</h1>
      <span class="rule"></span>
      {f"<p class='subtitle'>{html.escape(s['subtitle'])}</p>" if s.get('subtitle') else ''}
      {f"<div class='intro'>{page.body_html}</div>" if page.body_html else ''}
      {f"<p class='byline'>{html.escape(s['author'])}</p>" if s.get('author') else ''}
    </div>
  </section>"""


def render_overview(book: Book, colors: dict[str, str]) -> str:
    """Vertical milestone timeline: dotted centre line, entries alternate left / right."""
    n = len(book.pages)
    rows = []
    for i, p in enumerate(book.pages):
        side = "left" if i % 2 == 0 else "right"
        c = colors.get(p.phase, "var(--dark)")
        rows.append(f"""
      <li class="ov-item {side}" style="top:{i * 100 / n:.2f}%;--c:{c}">
        <span class="ov-circle"></span>
        <div class="ov-card">
          <span class="ov-num">{p.number:02d}</span>
          <p class="ov-meta">{meta_line(p)}</p>
          <p class="ov-title">{html.escape(p.title)}</p>
        </div>
      </li>""")
    return f"""
  <section class="sheet overview">
    <h2>{T['journey']}</h2>
    <span class="ov-start"></span>
    <ol class="ov-tl">{''.join(rows)}</ol>
  </section>"""


def page_head(page: Page) -> str:
    return f"""
      <header class="page-head">
        <span class="num">{page.number:02d}</span>
        <p class="meta">{meta_line(page)}</p>
        <h2>{html.escape(page.title)}</h2>
      </header>"""


def render_page(page: Page, img: dict[Path, str], book: Book, positions, colors) -> str:
    layout = choose_layout(page)
    color = colors.get(page.phase, "var(--dark)")
    imgs = [img[i] for i in page.images]
    head = page_head(page)
    caption = f'<p class="caption">{html.escape(page.meta["caption"])}</p>' if page.meta.get("caption") else ""
    body = f'<div class="body">{page.body_html}</div>' if page.body_html else ""

    if layout == "full" and imgs:
        content = f"""
      {figure(imgs[0], 'full-img')}
      <div class="full-overlay">{head}{caption}</div>"""
    elif layout == "text" or not imgs:
        content = f'<div class="text-only">{head}{body}{caption}</div>'
    else:
        n = len(imgs)
        grid_cls = f"media n{min(n, BOOK_MAX_IMAGES)}" + (" grid" if layout == "grid" else "")
        figs = "".join(figure(u) for u in imgs[:BOOK_MAX_IMAGES])
        if n > BOOK_MAX_IMAGES:
            print(f"  note: page {page.number} has {n} images - the book uses the first {BOOK_MAX_IMAGES}, "
                  f"the website gallery shows all")
        content = f"""
      <div class="{grid_cls}">{figs}</div>
      <div class="side">{head}{body}{caption}</div>"""

    return f"""
  <section class="sheet page layout-{layout}" style="--phase:{color}">
    <div class="content">{content}</div>
    <footer>{rail_html(book, page, positions, colors)}<span class="folio">{page.number}</span></footer>
  </section>"""


def render_back(page: Page, img: dict[Path, str], book: Book, positions, colors) -> str:
    pic = figure(img[page.images[0]], "back-img") if page.images else ""
    return f"""
  <section class="sheet back">
    <div class="back-inner">
      {pic}
      <div class="back-text">
        {f"<h2>{html.escape(page.meta['title'])}</h2>" if page.meta.get('title') else ''}
        {page.body_html}
      </div>
    </div>
    <footer>{rail_html(book, None, positions, colors)}</footer>
  </section>"""


def build_css(book: Book, size: tuple[float, float]) -> str:
    w, h = size
    th = theme(book)
    portrait = h > w
    lw = 0.8                  # bracket line width in mm
    full_h = h - 14 - 24      # content height in mm (top pad + footer zone)
    ch = round(full_h * 0.62, 2) if portrait else full_h   # height of the image area
    inner = ch - lw           # image area minus the bracket line on top
    gap = 2.5
    half = (inner - gap) / 2
    stack = (f".page .content {{ grid-template-columns: 1fr; grid-template-rows: {ch}mm 1fr; gap: 7mm; }}\n"
             if portrait else "")
    return f"""
@page {{ size: {w}mm {h}mm; margin: 0; }}
:root {{
  --w: {w}mm; --h: {h}mm;
  --paper: #FFFFFF; --ink: #141414; --muted: #5E656C; --soft: #F2F2F2;
  --line: {th['line']}; --line-ink: {th['line_ink']}; --dark: {th['dark']}; --accent: {th['accent']};
  --sans: {th['sans']};
  --pad: 14mm; --lw: {lw}mm;
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{ font-family: var(--sans); color: var(--ink); background: var(--paper);
  -webkit-print-color-adjust: exact; print-color-adjust: exact; }}

.sheet {{ width: var(--w); height: var(--h); position: relative; overflow: hidden;
  background: var(--paper); page-break-after: always; break-after: page; }}
.sheet:last-child {{ page-break-after: auto; break-after: auto; }}
figure {{ margin: 0; overflow: hidden; background: var(--soft); }}
figure img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
h1, h2 {{ font-weight: 700; margin: 0; }}
p {{ margin: 0 0 0.7em; }}
em {{ color: var(--muted); }}

/* ---------- cover: photo with a translucent white band on the left */
.cover .cover-img {{ position: absolute; inset: 0; }}
.cover-text {{ position: absolute; top: 0; bottom: 0; left: 18mm; width: 44%;
  padding: 0 11mm; display: flex; flex-direction: column; justify-content: center; }}
.cover.has-img .cover-text {{ background: rgba(255, 255, 255, 0.86); }}
.cover.no-img .cover-text {{ border-left: var(--lw) solid var(--line); left: 24mm; width: 60%; }}
.cover .kicker {{ font-size: 8pt; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase;
  color: var(--line-ink); margin-bottom: 5mm; }}
.cover h1 {{ font-size: 28pt; line-height: 1.08; text-transform: uppercase; color: var(--dark); letter-spacing: 0.005em; }}
.cover .rule {{ display: block; width: 18mm; height: var(--lw); background: var(--line); margin: 7mm 0 6mm; }}
.cover .subtitle {{ font-size: 13pt; line-height: 1.4; font-weight: 300; margin: 0; }}
.cover .intro {{ font-size: 9.5pt; line-height: 1.55; margin-top: 5mm; }}
.cover .byline {{ font-size: 8pt; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--muted); margin: 7mm 0 0; }}

/* ---------- overview: vertical milestones with a dotted centre line */
.overview h2 {{ position: absolute; top: var(--pad); left: 0; right: 0; text-align: center; font-size: 15pt;
  text-transform: uppercase; letter-spacing: 0.05em; color: var(--dark); }}
.ov-start {{ position: absolute; top: 27mm; left: 50%; margin-left: -2.4mm; width: 0; height: 0;
  border-left: 2.4mm solid transparent; border-right: 2.4mm solid transparent; border-top: 3mm solid var(--line); }}
.ov-tl {{ position: absolute; top: 38mm; bottom: 20mm; left: var(--pad); right: var(--pad);
  list-style: none; margin: 0; padding: 0; }}
.ov-tl::before {{ content: ""; position: absolute; left: 50%; top: -7mm; bottom: -8mm; margin-left: -0.35mm;
  border-left: 0.7mm dotted var(--line); }}
.ov-item {{ position: absolute; width: calc(50% - 20mm); }}
.ov-item.left {{ right: 50%; }}
.ov-item.right {{ left: 50%; }}
.ov-circle {{ position: absolute; top: -1.8mm; width: 4.4mm; height: 4.4mm; margin-top: 0.1mm; border-radius: 50%;
  background: var(--paper); border: 0.7mm solid var(--line); z-index: 1; }}
.left .ov-circle {{ right: -2.2mm; }}
.right .ov-circle {{ left: -2.2mm; }}
.ov-card {{ position: relative; border-top: var(--lw) solid var(--line); padding: 2mm 4mm 0.5mm; }}
.left .ov-card {{ border-left: var(--lw) solid var(--line); }}
.right .ov-card {{ border-right: var(--lw) solid var(--line); text-align: right; }}
.ov-num {{ position: absolute; top: calc(-1 * var(--lw)); width: 14mm; padding: 1.8mm 0; background: var(--line);
  color: #fff; font-size: 13pt; line-height: 1; text-align: center; }}
.left .ov-num {{ right: calc(100% + 2.5mm); }}
.right .ov-num {{ left: calc(100% + 2.5mm); }}
.ov-meta {{ font-size: 6.8pt; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--line-ink); margin: 0 0 0.8mm; min-height: 2.4mm; }}
.ov-meta .phase {{ color: var(--c); }}
.ov-title {{ font-size: 10.5pt; font-weight: 600; line-height: 1.2; margin: 0; }}

/* ---------- content pages */
.page .content {{ position: absolute; top: var(--pad); left: var(--pad); right: var(--pad);
  bottom: 24mm; display: grid; grid-template-columns: 1.55fr 1fr; gap: 9mm; }}
.page-head {{ position: relative; padding-left: 18mm; min-height: 12mm; margin-bottom: 6mm; }}
.page-head .num {{ position: absolute; left: 0; top: 0; width: 14mm; padding: 2mm 0; background: var(--line);
  color: #fff; font-size: 14pt; line-height: 1; text-align: center; }}
.page-head .meta {{ font-size: 7pt; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--line-ink); margin: 0.4mm 0 1.2mm; min-height: 2.5mm; }}
.page-head .phase {{ color: var(--phase); }}
.page-head h2 {{ font-size: 16pt; line-height: 1.15; color: var(--dark); }}
.body {{ position: relative; font-size: 9.8pt; line-height: 1.55; font-weight: 400;
  border-top: var(--lw) solid var(--line); border-left: var(--lw) solid var(--line); padding: 4mm 0 0 5mm; }}
.body::before {{ content: ""; position: absolute; top: -2.3mm; right: 0; width: 3.8mm; height: 3.8mm;
  border-radius: 50%; background: var(--paper); border: 0.7mm solid var(--line); }}
.body ul, .body ol {{ padding-left: 5mm; margin: 0 0 0.7em; }}
.body p:last-child {{ margin-bottom: 0; }}
.caption {{ font-size: 8.5pt; line-height: 1.45; color: var(--dark); background: var(--paper);
  border: var(--lw) solid var(--dark); padding: 2.6mm 3.6mm; margin: 5mm 0 0; }}
.side {{ overflow: hidden; }}

.media {{ display: grid; gap: {gap}mm; height: {ch}mm; border-top: var(--lw) solid var(--line);
  border-left: var(--lw) solid var(--line); }}
.media figure {{ height: 100%; }}
.media.n1 {{ grid-template-rows: {inner:.2f}mm; }}
.media.n2 {{ grid-template-rows: {half:.2f}mm {half:.2f}mm; }}
.media.n3 {{ grid-template-columns: 1fr 1fr; grid-template-rows: {(inner - gap) * 0.58:.2f}mm {(inner - gap) * 0.42:.2f}mm; }}
.media.n3 figure:first-child {{ grid-column: 1 / 3; }}
.media.n4 {{ grid-template-columns: 1fr 1fr; grid-template-rows: {half:.2f}mm {half:.2f}mm; }}

.layout-text .content {{ grid-template-columns: 1fr; }}
.text-only {{ max-width: 165mm; }}
.text-only .body {{ font-size: 11pt; max-width: 150mm; padding-right: 10mm; }}
.text-only .caption {{ max-width: 150mm; }}

.layout-full .content {{ inset: 0; bottom: 20mm; display: block; }}
.full-img {{ position: absolute; inset: 0; }}
.full-overlay {{ position: absolute; left: 18mm; bottom: 0; width: 44%; background: rgba(255, 255, 255, 0.9);
  padding: 8mm 9mm 7mm; }}
.full-overlay .page-head {{ margin-bottom: 0; }}

/* ---------- time rail: dotted line, a circle per page */
footer {{ position: absolute; left: var(--pad); right: var(--pad); bottom: 7mm; height: 10mm;
  display: flex; align-items: center; gap: 5mm; }}
.rail {{ flex: 1; display: flex; align-items: center; gap: 3mm; }}
.end-label {{ font-size: 6.5pt; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--line-ink); white-space: nowrap; }}
.track {{ position: relative; flex: 1; height: 0; border-top: 0.6mm dotted var(--line); }}
.fill {{ position: absolute; left: 0; top: -0.6mm; height: 0.6mm; background: var(--line); }}
.dot {{ position: absolute; top: -0.3mm; width: 2.4mm; height: 2.4mm; margin: -1.2mm 0 0 -1.2mm;
  border-radius: 50%; background: var(--paper); border: 0.5mm solid var(--line); }}
.dot.past {{ background: var(--line); }}
.dot.now {{ width: 4mm; height: 4mm; margin: -2mm 0 0 -2mm; background: var(--c); border: 0.7mm solid var(--paper);
  box-shadow: 0 0 0 0.35mm var(--line); }}
.here {{ position: absolute; bottom: 2.6mm; font-size: 6.5pt; font-weight: 700; letter-spacing: 0.08em;
  text-transform: uppercase; white-space: nowrap; color: var(--dark); }}
.here.mid {{ transform: translateX(-50%); }}
.here.end {{ transform: translateX(-100%); }}
.folio {{ font-size: 9pt; font-weight: 700; color: var(--dark); min-width: 6mm; text-align: right; }}

/* ---------- back: photo in a bracket, closing text in a navy block */
.back .back-inner {{ position: absolute; inset: var(--pad); bottom: 24mm; display: flex; gap: 10mm; align-items: flex-end; }}
.back-img {{ width: 38%; height: 72%; border-top: var(--lw) solid var(--line); border-left: var(--lw) solid var(--line); }}
.back-text {{ flex: 1; max-width: 140mm; background: var(--dark); color: #fff; padding: 9mm 11mm 8mm;
  font-size: 9.5pt; line-height: 1.55; }}
.back-text h2 {{ font-size: 15pt; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 5mm; }}
.back-text em {{ color: #D9E0E8; }}
.back-text a {{ color: #fff; }}
.back-text p:last-child {{ margin-bottom: 0; }}

/* ---------- screen preview only */
{stack}
@media screen {{
  body {{ background: #E6E6E6; padding: 72px 0 40px; }}
  .sheet {{ margin: 0 auto 28px; box-shadow: 0 2px 12px rgba(20, 20, 20, .18); }}
  .toolbar {{ position: fixed; top: 0; left: 0; right: 0; height: 52px; z-index: 10; display: flex; gap: 16px;
    align-items: center; padding: 0 24px; background: #fff; color: var(--ink); font-size: 14px;
    border-top: 4px solid var(--accent); box-shadow: 0 5px 7px -5px #999; }}
  .toolbar strong {{ text-transform: uppercase; letter-spacing: .04em; color: var(--dark); }}
  .toolbar span {{ color: var(--muted); }}
  .toolbar a, .toolbar button {{ font: inherit; font-weight: 600; background: none; color: var(--dark);
    border: 2px solid var(--dark); padding: 4px 12px; cursor: pointer; text-decoration: none; }}
  .toolbar a:hover, .toolbar button:hover {{ background: var(--dark); color: #fff; }}
  .toolbar a:focus-visible, .toolbar button:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
  .toolbar .grow {{ flex: 1; }}
  body.spread .book {{ display: flex; flex-wrap: wrap; justify-content: center; gap: 0 28px; }}
  body.spread .sheet {{ margin: 0 0 28px; zoom: .5; }}
}}
@media print {{ .toolbar {{ display: none; }} }}
"""


TOOLBAR = """
<div class="toolbar">
  <strong>{title}</strong><span>{count} {pages}</span><span class="grow"></span>
  <a href="index.html">{site}</a>
  <button type="button" onclick="document.body.classList.toggle('spread')">{toggle}</button>
  <button type="button" onclick="window.print()">{printlbl}</button>
</div>"""

LIVE_RELOAD = """
<script>
(function(){ let v=null; setInterval(async()=>{ try{
  const r=await fetch('_version.txt?'+Date.now()); const t=await r.text();
  if(v!==null && t!==v) location.reload(); v=t; }catch(e){} }, 1000); })();
</script>"""


def build_html(book: Book, out_dir: Path, size_name: str, max_px: int,
               overview: bool, for_screen: bool, live: bool = False) -> str:
    size = PAGE_SIZES[size_name]
    img = prepare_images(book, out_dir, max_px)
    colors = phase_colors(book)
    positions = rail_positions(book.pages, book.settings.get("rail", "dates").lower())
    parts = []
    if book.cover:
        parts.append(render_cover(book.cover, img, book))
    if overview and len(book.pages) > 1:
        parts.append(render_overview(book, colors))
    for p in book.pages:
        parts.append(render_page(p, img, book, positions, colors))
    if book.back:
        parts.append(render_back(book.back, img, book, positions, colors))
    title = html.escape(book.settings.get("title", book.root.name))
    toolbar = TOOLBAR.format(title=title, count=len(parts), pages=T['pages'], toggle=T['toggle'],
                             printlbl=T['print'], site=T['site']) if for_screen else ""
    return f"""<!doctype html>
<html lang="{html.escape(book.settings.get('lang', 'en'))}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
{FONTS_LINK}
<style>{build_css(book, size)}</style>
</head>
<body>{toolbar}
<main class="book">{''.join(parts)}
</main>{LIVE_RELOAD if live else ''}
</body>
</html>"""


# --------------------------------------------------------------------------- website
# The same content as one scrolling page: hero, milestone timeline, closing block.

def site_css(book: Book) -> str:
    th = theme(book)
    return f"""
:root {{
  --paper: #FFFFFF; --ink: #141414; --muted: #5E656C; --soft: #F2F2F2;
  --line: {th['line']}; --line-ink: {th['line_ink']}; --dark: {th['dark']}; --accent: {th['accent']};
  --sans: {th['sans']}; --lw: 3px;
}}
* {{ box-sizing: border-box; }}
html {{ scroll-behavior: smooth; }}
body {{ margin: 0; background: var(--paper); color: var(--ink); font-family: var(--sans);
  font-size: 17px; line-height: 1.6; -webkit-font-smoothing: antialiased; }}
img {{ display: block; max-width: 100%; }}
a {{ color: var(--accent); }}
em {{ color: var(--muted); }}

.topbar {{ position: sticky; top: 0; z-index: 20; background: #fff; border-top: 4px solid var(--accent);
  box-shadow: 0 5px 7px -5px #999; }}
.topbar-in {{ max-width: 1280px; margin: 0 auto; padding: 0 24px; height: 64px; display: flex; align-items: center; gap: 24px; }}
.brand {{ flex: 1; min-width: 0; font-weight: 700; font-size: 15px; letter-spacing: .04em; text-transform: uppercase;
  color: var(--dark); text-decoration: none; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.topbar nav {{ display: flex; gap: 22px; font-size: 14px; font-weight: 600; }}
.topbar nav a {{ color: var(--ink); text-decoration: none; padding: 6px 0; border-bottom: 2px solid transparent; white-space: nowrap; }}
.topbar nav a:hover {{ color: var(--accent); border-color: var(--accent); }}
a:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 3px; }}

.hero {{ position: relative; min-height: min(82vh, 800px); display: flex; background: var(--soft); }}
.hero-img {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }}
.hero-box {{ position: relative; margin-left: 70px; width: min(580px, calc(50% - 70px)); padding: 56px 46px;
  background: rgba(255, 255, 255, .86); display: flex; flex-direction: column; justify-content: center; }}
.kicker {{ margin: 0 0 16px; font-size: 13px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; color: var(--line-ink); }}
.hero h1 {{ margin: 0; font-size: clamp(30px, 3.4vw, 46px); line-height: 1.08; font-weight: 700;
  text-transform: uppercase; color: var(--dark); overflow-wrap: break-word; }}
.hero .rule {{ display: block; width: 64px; height: var(--lw); background: var(--line); margin: 26px 0 22px; }}
.lead {{ margin: 0; font-size: 21px; line-height: 1.45; font-weight: 300; }}
.hero .intro {{ margin-top: 18px; font-size: 16px; }}
.byline {{ margin: 22px 0 0; font-size: 13px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }}

.section-title {{ margin: 88px 16px 20px; text-align: center; font-size: 24px; font-weight: 700;
  letter-spacing: .05em; text-transform: uppercase; color: var(--dark); }}
.tl-start {{ width: 0; height: 0; margin: 0 auto; border-left: 14px solid transparent; border-right: 14px solid transparent;
  border-top: 18px solid var(--line); }}
.tl-end {{ width: 30px; height: var(--lw); margin: 0 auto 96px; background: var(--line); }}
.tl {{ position: relative; max-width: 1240px; margin: 0 auto; padding: 44px 24px 8px; list-style: none; }}
.tl::before {{ content: ""; position: absolute; top: 0; bottom: 0; left: 50%; margin-left: -1.5px; border-left: 3px dotted var(--line); }}
.tl-item {{ position: relative; width: 50%; margin-bottom: 48px; }}
.tl-item.left {{ margin-right: 50%; padding-left: 100px; }}
.tl-item.right {{ margin-left: 50%; padding-right: 100px; }}
.tl-item.img.left {{ padding-left: 0; }}
.tl-item.img.right {{ padding-right: 0; }}
.tl-circle {{ position: absolute; top: -11px; width: 25px; height: 25px; border-radius: 50%; background: #fff;
  border: 3px solid var(--line); z-index: 1; }}
.left .tl-circle {{ right: -12.5px; }}
.right .tl-circle {{ left: -12.5px; }}
.img .tl-circle {{ background: var(--line); }}

.tl-card {{ position: relative; border-top: var(--lw) solid var(--line); padding: 14px 22px 4px; }}
.left .tl-card {{ border-left: var(--lw) solid var(--line); }}
.right .tl-card {{ border-right: var(--lw) solid var(--line); text-align: right; }}
.tl-num {{ position: absolute; top: calc(-1 * var(--lw)); width: 84px; padding: 13px 0; background: var(--line);
  color: #fff; font-size: 30px; line-height: 1; text-align: center; }}
.left .tl-num {{ right: calc(100% + 10px); }}
.right .tl-num {{ left: calc(100% + 10px); }}
.tl-meta {{ margin: 0 0 4px; font-size: 13px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--line-ink); }}
.tl-meta .phase {{ color: var(--c); }}
.tl-title {{ margin: 0 0 10px; font-size: 23px; line-height: 1.2; font-weight: 700; color: var(--line-ink); }}
.tl-body p {{ margin: 0 0 .8em; }}

.tl-fig {{ margin: 0; width: fit-content; max-width: 100%; border-top: var(--lw) solid var(--line); }}
.left .tl-fig {{ margin-left: auto; padding-right: 80px; border-left: var(--lw) solid var(--line); }}
.right .tl-fig {{ margin-right: auto; padding-left: 80px; border-right: var(--lw) solid var(--line); }}
.gal {{ display: block; position: relative; width: 100%; padding: 0; border: 0; background: none; cursor: zoom-in; }}
.gal img {{ transition: opacity .2s ease; }}
.gal:hover img {{ opacity: .88; }}
.gal:focus-visible {{ outline: 3px solid var(--accent); outline-offset: 2px; }}
.gal-main img {{ max-height: 380px; width: auto; }}
.thumbs {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; margin-top: 6px; }}
.thumbs img {{ width: 100%; aspect-ratio: 4 / 3; object-fit: cover; }}
.gal[data-more]::after {{ content: attr(data-more); position: absolute; inset: 0; display: flex; align-items: center;
  justify-content: center; background: rgba(19, 49, 92, .62); color: #fff; font-size: 22px; font-weight: 700; }}

/* gallery pop-up */
.lb {{ width: 100vw; height: 100dvh; max-width: none; max-height: none; margin: 0; padding: 0; border: 0;
  background: #fff; color: var(--ink); }}
.lb[open] {{ display: grid; grid-template-rows: auto minmax(0, 1fr) auto auto; }}
.lb::backdrop {{ background: rgba(20, 20, 20, .6); }}
.lb-top {{ display: flex; align-items: center; gap: 16px; padding: 14px 20px; border-top: 4px solid var(--accent);
  box-shadow: 0 5px 7px -5px #999; }}
.lb-count {{ background: var(--line); color: #fff; font-size: 18px; line-height: 1; padding: 8px 12px; white-space: nowrap; }}
.lb-title {{ flex: 1; min-width: 0; margin: 0; font-weight: 700; font-size: 17px; text-transform: uppercase;
  letter-spacing: .04em; color: var(--dark); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.lb button {{ font: inherit; cursor: pointer; }}
.lb-close {{ border: 2px solid var(--dark); background: #fff; color: var(--dark); font-weight: 600; font-size: 14px; padding: 6px 14px; }}
.lb-close:hover {{ background: var(--dark); color: #fff; }}
.lb-stage {{ position: relative; display: flex; align-items: center; justify-content: center; min-height: 0; padding: 24px 76px; }}
.lb-frame {{ max-width: 100%; max-height: 100%; min-height: 0;
  border-top: var(--lw) solid var(--line); border-left: var(--lw) solid var(--line); }}
.lb-img {{ max-width: 100%; max-height: calc(100dvh - 300px); width: auto; height: auto; }}
.lb-prev, .lb-next {{ position: absolute; top: 50%; width: 52px; height: 52px; margin-top: -26px; border-radius: 50%;
  border: 3px solid var(--line); background: #fff; color: var(--dark); font-size: 28px; line-height: 1; }}
.lb-prev {{ left: 14px; }}
.lb-next {{ right: 14px; }}
.lb-prev:hover, .lb-next:hover {{ background: var(--line); color: #fff; }}
.lb button:focus-visible {{ outline: 3px solid var(--accent); outline-offset: 2px; }}
.lb-cap {{ justify-self: center; max-width: min(760px, calc(100% - 32px)); margin: 0 0 14px; padding: 12px 20px;
  border: var(--lw) solid var(--dark); color: var(--dark); font-size: 15px; }}
.lb-cap[hidden] {{ display: none; }}
.lb-strip {{ display: flex; gap: 6px; justify-content: center; padding: 0 16px 18px; overflow-x: auto; }}
.lb-strip button {{ flex: none; padding: 0; border: 3px solid transparent; background: none; }}
.lb-strip button[aria-current="true"] {{ border-color: var(--line); }}
.lb-strip img {{ width: 84px; height: 60px; object-fit: cover; }}
.lb.single .lb-prev, .lb.single .lb-next, .lb.single .lb-strip {{ display: none; }}
.tl-cap {{ width: fit-content; max-width: calc(100% - 80px); margin: 0; padding: 18px 24px; font-size: 15px; line-height: 1.5;
  color: var(--dark); background: #fff; border: var(--lw) solid var(--dark); }}
.left .tl-cap {{ margin-left: auto; margin-right: 80px; }}
.right .tl-cap {{ margin-left: 80px; }}

.closing {{ max-width: 1120px; margin: 0 auto 96px; padding: 0 24px; display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.15fr); gap: 48px; align-items: end; }}
.closing.no-img {{ grid-template-columns: minmax(0, 760px); justify-content: center; }}
.closing-fig {{ margin: 0; border-top: var(--lw) solid var(--line); border-left: var(--lw) solid var(--line); }}
.closing-fig img {{ width: 100%; aspect-ratio: 4 / 3; object-fit: cover; }}
.closing-box {{ background: var(--dark); color: #fff; padding: 40px 44px; font-size: 16px; }}
.closing-box h2 {{ margin: 0 0 18px; font-size: 24px; letter-spacing: .04em; text-transform: uppercase; }}
.closing-box p:last-child {{ margin-bottom: 0; }}
.closing-box em {{ color: #D9E0E8; }}
.closing-box a {{ color: #fff; }}

.site-foot {{ background: var(--soft); padding: 28px 24px; text-align: center; font-size: 13px; color: var(--muted); }}

.js .tl-item {{ opacity: 0; transform: translateY(60px); }}
.js .tl-item.on {{ opacity: 1; transform: none; transition: opacity .75s ease, transform .75s ease; }}
@media (prefers-reduced-motion: reduce) {{
  .js .tl-item {{ opacity: 1; transform: none; transition: none; }}
  html {{ scroll-behavior: auto; }}
}}

@media (max-width: 900px) {{
  .topbar-in {{ padding: 0 16px; gap: 14px; }}
  .topbar nav {{ gap: 14px; font-size: 13px; }}
  .hero {{ display: block; min-height: 0; }}
  .hero-img {{ position: static; height: 58vw; max-height: 420px; }}
  .hero-box {{ width: auto; margin: 0; padding: 32px 16px 8px; background: #fff; }}
  .section-title {{ margin-top: 56px; }}
  .tl {{ padding: 36px 16px 8px; }}
  .tl::before {{ left: 26px; }}
  .tl-item, .tl-item.left, .tl-item.right, .tl-item.img.left, .tl-item.img.right {{
    width: 100%; margin: 0 0 40px; padding: 0 0 0 40px; }}
  .left .tl-circle, .right .tl-circle {{ left: -2.5px; right: auto; }}
  .left .tl-card, .right .tl-card {{ border-left: var(--lw) solid var(--line); border-right: 0; text-align: left; padding: 14px 16px 4px; }}
  .tl-num {{ position: static; display: inline-block; width: auto; padding: 8px 12px; margin-bottom: 10px; font-size: 22px; }}
  .tl-title {{ font-size: 20px; }}
  .left .tl-fig, .right .tl-fig {{ width: auto; margin: 0; padding: 0; border-left: var(--lw) solid var(--line); border-right: 0; }}
  .gal-main img {{ width: 100%; max-height: none; }}
  .lb-stage {{ padding: 12px 8px; }}
  .lb-img {{ max-height: calc(100dvh - 240px); }}
  .lb-prev, .lb-next {{ width: 42px; height: 42px; margin-top: -21px; font-size: 22px; }}
  .lb-prev {{ left: 6px; }}
  .lb-next {{ right: 6px; }}
  .lb-strip {{ justify-content: flex-start; }}
  .lb-strip img {{ width: 64px; height: 46px; }}
  .left .tl-cap, .right .tl-cap {{ width: auto; max-width: none; margin: 0; padding: 14px 16px; }}
  .closing, .closing.no-img {{ grid-template-columns: minmax(0, 1fr); gap: 24px; padding: 0 16px; margin-bottom: 64px; }}
  .closing-box {{ padding: 28px 22px; }}
}}
"""


REVEAL = """
<script>
(function(){
  var items = document.querySelectorAll('.tl-item');
  function show(e){ e.classList.add('on'); }
  if (!('IntersectionObserver' in window) || matchMedia('(prefers-reduced-motion: reduce)').matches) {
    items.forEach(show); return;
  }
  var io = new IntersectionObserver(function(entries){
    entries.forEach(function(en){ if (en.isIntersecting) { show(en.target); io.unobserve(en.target); } });
  }, { rootMargin: '0px 0px -8% 0px' });
  items.forEach(function(e){ io.observe(e); });
})();
</script>"""


LIGHTBOX = """
<dialog class="lb" aria-labelledby="lb-title">
  <div class="lb-top">
    <span class="lb-count"></span>
    <p class="lb-title" id="lb-title"></p>
    <button type="button" class="lb-close">{close}</button>
  </div>
  <div class="lb-stage">
    <button type="button" class="lb-prev" aria-label="{prev}">&#8249;</button>
    <div class="lb-frame"><img class="lb-img" alt=""></div>
    <button type="button" class="lb-next" aria-label="{next}">&#8250;</button>
  </div>
  <p class="lb-cap"></p>
  <div class="lb-strip"></div>
</dialog>
<script>
(function(){{
  var dlg = document.querySelector('.lb'), src = document.getElementById('gallery-data');
  if (!dlg || !src || !dlg.showModal) return;
  var data = JSON.parse(src.textContent), g = null, i = 0, opener = null, x0 = null;
  var img = dlg.querySelector('.lb-img'), count = dlg.querySelector('.lb-count'),
      title = dlg.querySelector('.lb-title'), cap = dlg.querySelector('.lb-cap'), strip = dlg.querySelector('.lb-strip');
  function show(k) {{
    var n = g.images.length;
    i = (k + n) % n;
    img.src = g.images[i];
    img.alt = g.title + ' (' + (i + 1) + '/' + n + ')';
    count.textContent = (i + 1) + ' / ' + n;
    strip.querySelectorAll('button').forEach(function(b, j){{ b.setAttribute('aria-current', j === i ? 'true' : 'false'); }});
    var cur = strip.children[i]; if (cur) cur.scrollIntoView({{ block: 'nearest', inline: 'nearest' }});
  }}
  function open(key, k, from) {{
    g = data[key]; if (!g) return;
    opener = from;
    title.textContent = g.title;
    cap.textContent = g.caption || ''; cap.hidden = !g.caption;
    dlg.classList.toggle('single', g.images.length < 2);
    strip.innerHTML = '';
    g.images.forEach(function(u, j){{
      var b = document.createElement('button'); b.type = 'button';
      b.setAttribute('aria-label', (j + 1) + ' / ' + g.images.length);
      var t = document.createElement('img'); t.src = u; t.alt = ''; b.appendChild(t);
      b.addEventListener('click', function(){{ show(j); }});
      strip.appendChild(b);
    }});
    dlg.showModal();
    show(k);
  }}
  document.addEventListener('click', function(e){{
    var b = e.target.closest('[data-g]');
    if (b) open(b.getAttribute('data-g'), +b.getAttribute('data-i') || 0, b);
  }});
  dlg.querySelector('.lb-prev').addEventListener('click', function(){{ show(i - 1); }});
  dlg.querySelector('.lb-next').addEventListener('click', function(){{ show(i + 1); }});
  dlg.querySelector('.lb-close').addEventListener('click', function(){{ dlg.close(); }});
  dlg.addEventListener('click', function(e){{ if (e.target === dlg) dlg.close(); }});
  dlg.addEventListener('keydown', function(e){{
    if (e.key === 'ArrowLeft') {{ show(i - 1); e.preventDefault(); }}
    if (e.key === 'ArrowRight') {{ show(i + 1); e.preventDefault(); }}
  }});
  dlg.addEventListener('close', function(){{ img.removeAttribute('src'); if (opener) opener.focus(); }});
  var stage = dlg.querySelector('.lb-stage');
  stage.addEventListener('touchstart', function(e){{ x0 = e.touches[0].clientX; }}, {{ passive: true }});
  stage.addEventListener('touchend', function(e){{
    if (x0 === null) return;
    var dx = e.changedTouches[0].clientX - x0; x0 = null;
    if (Math.abs(dx) > 50) show(i + (dx < 0 ? 1 : -1));
  }});
}})();
</script>"""

MAX_THUMBS = 3   # small photos under the main one on the website; the rest are in the gallery


def gallery_figure(key: str, urls: list[str], title: str) -> str:
    """Main photo + up to MAX_THUMBS thumbnails; every photo opens the gallery pop-up at itself."""
    label = html.escape(f"{T['gallery']}: {title} ({len(urls)} {T['photos']})")
    main = (f'<button type="button" class="gal gal-main" data-g="{key}" data-i="0" aria-label="{label}">'
            f'<img src="{html.escape(urls[0])}" alt="{html.escape(title)}" loading="lazy"></button>')
    rest = urls[1:1 + MAX_THUMBS]
    hidden = len(urls) - 1 - len(rest)
    thumbs = ""
    for k, u in enumerate(rest, 1):
        more = f' data-more="+{hidden}"' if hidden and k == len(rest) else ""
        thumbs += (f'<button type="button" class="gal" data-g="{key}" data-i="{k}" aria-label="{label}"{more}>'
                   f'<img src="{html.escape(u)}" alt="" loading="lazy"></button>')
    if thumbs:
        thumbs = f'<div class="thumbs">{thumbs}</div>'
    return f'<figure class="tl-fig">{main}{thumbs}</figure>'


def pdf_name_for(book: Book, args) -> str:
    return getattr(args, "pdf_name", None) or \
        re.sub(r"[^\w\-]+", "_", book.settings.get("title", book.root.name)).strip("_") + ".pdf"


def build_site_html(book: Book, out_dir: Path, max_px: int, pdf_name: str | None, live: bool = False) -> str:
    img = prepare_images(book, out_dir, max_px)
    colors = phase_colors(book)
    s = book.settings
    title = html.escape(s.get("title", book.root.name))

    items, side, galleries = [], 0, {}
    for p in book.pages:
        c = colors.get(p.phase, "var(--dark)")
        cls = ("left", "right")[side % 2]
        body = f'<div class="tl-body">{p.body_html}</div>' if p.body_html else ""
        items.append(f"""
    <li class="tl-item text {cls}" style="--c:{c}">
      <span class="tl-circle"></span>
      <div class="tl-card">
        <span class="tl-num">{p.number:02d}</span>
        <p class="tl-meta">{meta_line(p)}</p>
        <h3 class="tl-title">{html.escape(p.title)}</h3>
        {body}
      </div>
    </li>""")
        side += 1
        if p.images:
            cls = ("left", "right")[side % 2]
            key = f"p{p.number}"
            urls = [img[f] for f in p.images]
            galleries[key] = {"title": f"{p.number:02d} · {p.title}", "caption": p.meta.get("caption", ""),
                              "images": urls}
            cap = f'<p class="tl-cap">{html.escape(p.meta["caption"])}</p>' if p.meta.get("caption") else ""
            items.append(f"""
    <li class="tl-item img {cls}">
      <span class="tl-circle"></span>
      {gallery_figure(key, urls, p.title)}
      {cap}
    </li>""")
            side += 1

    hero_img = ""
    if book.cover and book.cover.images:
        hero_img = f'<img class="hero-img" src="{html.escape(img[book.cover.images[0]])}" alt="">'
    span = cover_span(book)
    intro = book.cover.body_html if book.cover else ""

    closing = ""
    if book.back:
        b = book.back
        pic = (f'<figure class="closing-fig"><img src="{html.escape(img[b.images[0]])}" alt="" loading="lazy"></figure>'
               if b.images else "")
        closing = f"""
  <section class="closing {'' if pic else 'no-img'}">
    {pic}
    <div class="closing-box">
      {f"<h2>{html.escape(b.meta['title'])}</h2>" if b.meta.get('title') else ''}
      {b.body_html}
    </div>
  </section>"""

    pdf_link = f'<a href="{html.escape(pdf_name)}">{T["pdf"]}</a>' if pdf_name else ""
    gallery_json = json.dumps(galleries, ensure_ascii=False).replace("</", "<\\/")
    foot = " · ".join(html.escape(x) for x in (s.get("title", ""), span, s.get("author", "")) if x)

    return f"""<!doctype html>
<html lang="{html.escape(s.get('lang', 'en'))}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
{f'<meta name="description" content="{html.escape(s["subtitle"])}">' if s.get("subtitle") else ''}
<script>document.documentElement.classList.add('js');</script>
{FONTS_LINK}
<style>{site_css(book)}</style>
</head>
<body>
<header class="topbar"><div class="topbar-in">
  <a class="brand" href="#top">{title}</a>
  <nav><a href="#pot">{T['journey']}</a><a href="preview.html">{T['book']}</a>{pdf_link}</nav>
</div></header>
<main id="top">
  <section class="hero">
    {hero_img}
    <div class="hero-box">
      {f'<p class="kicker">{html.escape(span)}</p>' if span else ''}
      <h1>{title}</h1>
      <span class="rule"></span>
      {f'<p class="lead">{html.escape(s["subtitle"])}</p>' if s.get('subtitle') else ''}
      {f'<div class="intro">{intro}</div>' if intro else ''}
      {f'<p class="byline">{html.escape(s["author"])}</p>' if s.get('author') else ''}
    </div>
  </section>
  <h2 class="section-title" id="pot">{T['journey']}</h2>
  <div class="tl-start"></div>
  <ol class="tl">{''.join(items)}
  </ol>
  <div class="tl-end"></div>
  {closing}
</main>
<footer class="site-foot">{foot}</footer>
<script type="application/json" id="gallery-data">{gallery_json}</script>
{LIGHTBOX.format(close=T['close'], prev=T['prev'], next=T['next'])}
{REVEAL}{LIVE_RELOAD if live else ''}
</body>
</html>"""


# --------------------------------------------------------------------------- commands

def out_dir_for(root: Path, args) -> Path:
    d = Path(args.out) if getattr(args, "out", None) else root / "_output"
    d.mkdir(parents=True, exist_ok=True)
    copy_fonts(d)
    return d


def write_site(book: Book, out: Path, args, live=False) -> Path:
    pdf = pdf_name_for(book, args)
    path = out / "index.html"
    path.write_text(build_site_html(book, out, args.max_px, pdf if (out / pdf).exists() else None, live),
                    encoding="utf-8")
    return path


def write_preview(root: Path, args, live=False) -> Path:
    """Writes the book preview (preview.html) and the website (index.html)."""
    book = load_book(root)
    out = out_dir_for(root, args)
    html_text = build_html(book, out, args.size, args.max_px, not args.no_overview, True, live)
    path = out / "preview.html"
    path.write_text(html_text, encoding="utf-8")
    write_site(book, out, args, live)
    (out / "_version.txt").write_text(str(time.time()))
    return path


def write_pdf(root: Path, args) -> Path:
    try:
        from weasyprint import HTML
    except ImportError:
        sys.exit("WeasyPrint is not installed. Run: pip install -r requirements.txt")
    book = load_book(root)
    out = out_dir_for(root, args)
    html_text = build_html(book, out, args.size, args.max_px, not args.no_overview, False)
    (out / "print.html").write_text(html_text, encoding="utf-8")
    pdf_path = out / pdf_name_for(book, args)
    print("  rendering PDF ...")
    HTML(string=html_text, base_url=str(out)).write_pdf(str(pdf_path))
    return pdf_path


def snapshot(root: Path) -> str:
    h = hashlib.md5()
    for p in sorted(root.rglob("*")):
        if "_output" in p.parts or p.name.startswith("."):
            continue
        if p.is_file():
            st = p.stat()
            h.update(f"{p}|{st.st_mtime}|{st.st_size}".encode())
    return h.hexdigest()


def cmd_preview(args):
    root = Path(args.folder)
    path = write_preview(root, args, live=args.watch)
    print(f"  preview: {path}")
    print(f"  website: {path.parent / 'index.html'}")
    if not args.watch:
        if not args.no_open:
            webbrowser.open(path.resolve().as_uri())
        return
    out = path.parent

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(out), **kw)

        def log_message(self, *a):
            pass

    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", args.port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{args.port}/preview.html"
    print(f"  live preview at {url}  (edit files - the page reloads; Ctrl+C to stop)")
    print(f"  website at      http://127.0.0.1:{args.port}/index.html")
    if not args.no_open:
        webbrowser.open(url)
    last = snapshot(root)
    try:
        while True:
            time.sleep(1)
            now = snapshot(root)
            if now != last:
                last = now
                try:
                    write_preview(root, args, live=True)
                    print(f"  rebuilt {time.strftime('%H:%M:%S')}")
                except Exception as e:  # keep watching even if a file is half-written
                    print(f"  build error: {e}")
    except KeyboardInterrupt:
        httpd.shutdown()


def cmd_pdf(args):
    path = write_pdf(Path(args.folder), args)
    print(f"  PDF: {path}")


def cmd_site(args):
    root = Path(args.folder)
    book = load_book(root)
    path = write_site(book, out_dir_for(root, args), args)
    print(f"  website: {path}")
    if not args.no_open:
        webbrowser.open(path.resolve().as_uri())


def cmd_build(args):
    root = Path(args.folder)
    print(f"  PDF: {write_pdf(root, args)}")   # first, so the website can link to it
    path = write_preview(root, args)
    print(f"  preview: {path}")
    print(f"  website: {path.parent / 'index.html'}")


def cmd_init(args):
    root = Path(args.folder)
    if root.exists() and any(root.iterdir()):
        sys.exit(f"{root} already exists and is not empty")
    example = [
        ("cover", "---\ntitle: Oak Bench\nsubtitle: A garden bench, from sketch to seat\nauthor: Your Name\naccent: #2F55A4\n---\n", 1),
        ("1", "---\ntitle: The idea\ndate: 2025-01-12\nphase: Idea\n---\nIt started with an empty corner of the garden and a fallen oak.\n\nThe goal: a bench that seats three and lasts twenty years.", 1),
        ("2", "---\ntitle: First sketches\ndate: 2025-01-26\nphase: Design\ncaption: Pencil sketches, versions 1 to 3\n---\nThree directions: a slab bench, a slatted bench and a curved one.", 3),
        ("3", "---\ntitle: Cardboard model\ndate: 2025-02-20\nphase: Prototype\n---\nA 1:5 model showed the curved version was too hard to build.", 2),
        ("4", "---\ntitle: Choosing the wood\ndate: 2025-03-08\nphase: Design\nlayout: text\n---\nOak was dried for six weeks. Moisture target: under 12 %.\n\n- Seat: 40 mm oak planks\n- Legs: 70 x 70 mm\n- Finish: hard wax oil", 0),
        ("5", "---\ntitle: Joinery\ndate: 2025-04-15\nphase: Build\n---\nMortise-and-tenon joints, no metal fixings.", 4),
        ("6", "---\ntitle: In the garden\ndate: 2025-06-01\nphase: Finish\nlayout: full\ncaption: First summer evening on the bench\n---\n", 1),
        ("back", "---\ntitle: Thank you\n---\nTo everyone who helped carry the log.\n\nPhotos and text by Your Name, 2025.", 1),
    ]
    for name, text, n_img in example:
        d = root / name
        d.mkdir(parents=True)
        (d / "page.md").write_text(text, encoding="utf-8")
        for k in range(n_img):
            make_placeholder(d / f"{k + 1:02d}.jpg", f"{name} / image {k + 1}", k)
    print(f"  example project created in {root}")
    print(f"  next: python bookbuilder.py preview {root} --watch")


def make_placeholder(path: Path, label: str, k: int):
    if Image is None:
        return
    from PIL import ImageDraw
    tones = [(196, 205, 214), (205, 200, 188), (190, 206, 196), (214, 198, 196)]
    im = Image.new("RGB", (1600, 1100), tones[k % len(tones)])
    d = ImageDraw.Draw(im)
    for x in range(0, 1600, 80):
        d.line([(x, 0), (x, 1100)], fill=tuple(c - 12 for c in tones[k % len(tones)]), width=2)
    d.text((60, 60), label, fill=(60, 64, 70))
    im.save(path, "JPEG", quality=85)


def main():
    # consoles with a legacy code page (cp1252) can't print č/š/ž - never crash on a status message
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap =argparse.ArgumentParser(description="Build a 'from idea to finish' project book from folders.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("folder", help="project folder containing cover/, 1/, 2/, ..., back/")
        p.add_argument("-o", "--out", help="output folder (default: <folder>/_output)")
        p.add_argument("--size", default="a4-landscape", choices=PAGE_SIZES.keys())
        p.add_argument("--max-px", type=int, default=2400, help="downscale images to this size (default 2400)")
        p.add_argument("--no-overview", action="store_true", help="skip the timeline overview page")

    p = sub.add_parser("preview", help="build HTML preview")
    common(p)
    p.add_argument("--watch", action="store_true", help="serve locally and rebuild on changes")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-open", action="store_true", help="don't open the browser")
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("pdf", help="build final PDF")
    common(p)
    p.add_argument("--pdf-name", help="file name of the PDF")
    p.set_defaults(func=cmd_pdf)

    p = sub.add_parser("site", help="build the one-page website (index.html)")
    common(p)
    p.add_argument("--pdf-name", help="PDF to link to from the website")
    p.add_argument("--no-open", action="store_true", help="don't open the browser")
    p.set_defaults(func=cmd_site)

    p = sub.add_parser("build", help="build website, HTML preview and PDF")
    common(p)
    p.add_argument("--pdf-name")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("init", help="create an example project folder")
    p.add_argument("folder")
    p.set_defaults(func=cmd_init)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
