# Prompt for Claude Code

Copy everything below the line into Claude Code, started in an empty folder
(or in the folder with `bookbuilder.py` if you want it to extend the existing version).

---

You are building **Book Builder**, a local tool that turns a folder of project material into a
"from idea to finish" process book: a chronological, photo-led book that documents how one project
developed over time. The user checks the book in an HTML preview and exports the final PDF.

If `bookbuilder.py` already exists in this folder, read it and README.md first and treat them as
version 1: keep the folder conventions and CLI compatible, refactor into a package, and add the
features below. Otherwise build from scratch.

## Input: folder conventions (must not change)

```
my_project/
  cover/      first page of the book; its page.md also holds book-wide settings
  1/  2/  3/  content pages, ordered by the leading number of the folder name
              ("1", "02", "03 prototype" are all valid; sort numerically, not alphabetically)
  back/       last page of the book
```

- Accept aliases: cover|main|front|naslovnica and back|last|end|zadnja (case-insensitive).
- Ignore folders starting with `_` or `.`; warn about folders without a leading number.
- In each folder: images (jpg, jpeg, png, webp, gif, tif; natural sort by file name) and one
  text file (`page.md`, fallback: first *.md or *.txt).
- `page.md` has optional front matter between `---` lines with keys: title, date, phase, layout
  (auto|text|split|grid|full), caption. Body is Markdown.
- Cover settings: title, subtitle, author, span, accent (hex), font, lang.
- Dates may be `YYYY`, `YYYY-MM`, `YYYY-MM-DD` or `DD.MM.YYYY`; anything else is shown as text only.
- Optional per-image captions: a `captions.txt` in the page folder with lines `filename: caption`.

## Output

1. `_output/preview.html` – one self-contained page in the browser showing all book pages as
   sheets at true size, a toolbar (title, page count, toggle 2-up spread view, print) and live
   reload when files in the project folder change (`--watch`, local http server, polling).
2. `_output/<title>.pdf` – the final book, rendered from the **same HTML/CSS** with WeasyPrint so
   preview and PDF match. Use CSS `@page` for size, one `.sheet` element per page.

## Book structure and design

- Cover (full-bleed photo with a title block, or typographic cover without photo).
- Optional overview page "The journey": every page listed with date, phase colour dot, title, page number.
- Content pages: page number, date, phase, title, text and 1–6 images. Auto layout chooses by
  image count; `layout` in front matter overrides. Landscape formats put images left and text
  right; portrait formats stack images above text.
- Signature element: a **time rail** at the bottom of every content page – a thin line from the
  first to the last project date, one dot per page positioned by real date (undated pages are
  interpolated, fall back to even spacing), the current page highlighted in its phase colour,
  the elapsed part filled, date and phase label above the current dot.
- Back page: optional image, closing text (credits, thanks, contact), full rail with no highlight.
- Each phase gets a colour from a fixed, print-safe palette; accent colour configurable.
- Typography: one serif for text, one condensed sans for dates, labels and the rail; system
  fallbacks so the PDF still works offline. Minimum 9 pt body text, generous margins.
- Images are copied into `_output/_img`, auto-rotated from EXIF, downscaled to `--max-px`
  (default 2400) and cached by hash so rebuilds are fast; images are cropped with object-fit: cover.

## CLI

```
bookbuilder init    <folder>            # example project with generated placeholder images
bookbuilder preview <folder> [--watch] [--port 8765] [--no-open]
bookbuilder pdf     <folder> [--pdf-name x.pdf]
bookbuilder build   <folder>            # preview + pdf
common: -o/--out, --size a4-landscape|a4|a5-landscape|square|letter-landscape|letter,
        --max-px, --no-overview
```

## New features for this version

1. **Print-shop mode** `--print`: 3 mm bleed on all sides (`@page { bleed: 3mm; marks: crop }`),
   images that touch the page edge extend into the bleed, 300 dpi check – warn when an image
   would print below 200 dpi at its placed size.
2. **Validation report** before building: missing text files, pages without date, dates out of
   order, empty folders, images over 6 per page, very long text that will overflow the text
   column (estimate by character count per layout). Print as a clear list; `--strict` makes
   warnings fatal.
3. **Text overflow handling**: if body text does not fit, shrink font down to 9 pt, then warn.
4. **Page count helper**: printed books need page counts divisible by 4 (saddle stitch) or
   by 2; report the count and optionally add blank pages before the back cover (`--pad-to 4`).
5. **Config file** `book.toml` in the project root as an alternative to cover front matter.
6. **Simple GUI** (optional, `bookbuilder gui`): a small local web page served by the same server
   where the user picks the project folder, sees the preview, and clicks "Export PDF". No
   Electron; plain HTML + a few endpoints.

## Engineering requirements

- Python 3.10+, dependencies: weasyprint, markdown, Pillow (and tomli on 3.10). Nothing else
  without asking me first.
- Package layout: `bookbuilder/` with `model.py`, `loader.py`, `images.py`, `timeline.py`,
  `render.py` (HTML), `templates/` (CSS and HTML templates as real files, not strings),
  `cli.py`, `server.py`; `pyproject.toml` with a `bookbuilder` console script.
- Escape all user text in HTML. Handle non-ASCII (č, š, ž) in folder names, titles and file names.
- Tests with pytest: folder ordering and aliases, front matter parsing, date parsing, rail
  positions (dated, partially dated, undated), layout choice, and one end-to-end test that
  runs `init` + `build` in a temp dir and checks the PDF page count with pypdf.
- After building, render every PDF page to PNG (pdftoppm or pypdfium2), look at the images
  yourself and fix any overflow, overlap or clipping before you report done.
- README with install notes for Windows, macOS and Linux (WeasyPrint needs Pango/GTK).

## Definition of done

`bookbuilder init demo && bookbuilder build demo` produces a preview and a PDF where every page
matches the preview, nothing overflows or overlaps, the time rail is correct on every page,
all tests pass, and the validation report is clean for the demo project.
Work step by step, commit after each working step, and ask me before changing the folder conventions.
