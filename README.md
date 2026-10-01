# Book Builder – project book "from idea to finish"

Turns a folder of subfolders (cover, numbered pages, back) into a printable book
and a one-page website. You check the book in an HTML preview in the browser,
then export the final PDF. Every content page carries a **time rail** at the bottom
that shows where that moment sits between the first and last date of the project.

## Style

Book and website share one visual language, modelled on the history timeline at
sagradafamilia.org: Inter throughout, white paper, a dotted sand-coloured timeline
with circles, sand L-shaped brackets around text and photos, sand number boxes,
navy headings, navy-framed captions and a navy closing block.

- **Book:** cover photo with a white band, a "milestones" overview page (vertical timeline,
  entries alternate left / right), content pages, back page.
- **Website** (`_output/index.html`): hero photo with the title, then every page as a
  milestone on a vertical timeline (text and photos alternate sides, fade in on scroll),
  closing block, links to the book preview and the PDF. Single column on phones.
  Clicking a photo opens a gallery of all photos of that page.

## Install

```bash
pip install -r requirements.txt
```

WeasyPrint needs Pango on the system. On Windows install GTK3 runtime first, on macOS
`brew install pango`, on Ubuntu/Debian `sudo apt install libpango-1.0-0 libpangoft2-1.0-0`.
See https://doc.courtbouillon.org/weasyprint/stable/first_steps.html

## Offline

Everything runs locally: building, live preview (`127.0.0.1`), the website and the PDF.
The **Inter** font is bundled in `fonts/` (SIL Open Font License, see `fonts/OFL.txt`) and copied
into every `_output/fonts/`, so the book and the website look the same with or without internet
and the website makes no requests to other servers. Keep the `fonts/` folder next to `bookbuilder.py`.
The finished `_output/` folder is self-contained – copy it anywhere and open `index.html`.

## Quick start

```bash
python bookbuilder.py init my_project              # example project with placeholder images
python bookbuilder.py preview my_project --watch   # live preview, reloads when you edit files
python bookbuilder.py pdf my_project               # final PDF in my_project/_output/
python bookbuilder.py site my_project              # website only (index.html)
python bookbuilder.py build my_project             # PDF + book preview + website
```

## Folder structure

```
my_project/
  cover/        first page; page.md here also holds book settings
  1/            page 1   ("1", "01", "03 prototype" all work – sorted by the leading number)
  2/
  ...
  back/         last page
```

Folder names accepted for the first page: `cover`, `main`, `front`, `naslovnica`.
For the last page: `back`, `last`, `end`, `zadnja`. Folders starting with `_` or `.` are ignored.

Each folder contains images (jpg, png, webp, gif, tif – sorted by file name) and one
text file, `page.md`:

```markdown
---
title: First prototype
date: 2025-03-14
phase: Prototype
layout: auto
caption: Cardboard mock-up, version 2
---
Markdown text for the page. **Bold**, lists, links all work.
```

| Field | Meaning |
|---|---|
| `title` | Page heading |
| `date` | `YYYY`, `YYYY-MM`, `YYYY-MM-DD` or `DD.MM.YYYY`. Places the page on the time rail. Free text ("Summer 2024") is shown but not placed. |
| `phase` | Idea, Research, Design, Prototype, Build, Finish … Each phase gets its own colour. |
| `layout` | `auto` (default), `text`, `split`, `grid`, `full` (one image over the whole page) |
| `caption` | Small caption under the text |

Cover-only settings in `cover/page.md`: `title`, `subtitle`, `author`, `span`
(overrides the automatic date range), `accent` (hex colour), `font`,
`lang` (`en` or `sl` – month names and labels), `palette` (comma-separated phase colours),
`line` (bracket / timeline colour, default `#D3B59F`), `dark` (headings, captions, default `#13315C`),
`rail` (`dates` = dots placed by real date, `even` = evenly spaced steps),
`rail_start` / `rail_end` (labels at the ends of the time rail).

Auto layout: no images → text page; one image and no text → full-bleed; 1–2 images → images + text column; 3 or more → image grid + text column.

**Photos:** put as many photos in a page folder as you like. The book uses the **first 4**
(sorted by file name, so name the best ones `01…`, `02…`). The website shows the first photo
large with up to 3 thumbnails under it (a "+N" badge when there are more); clicking any of them
opens a gallery pop-up with **all** photos of that page: arrows, ← → keys, swipe on phones,
thumbnail strip, Esc or "Close" to exit.

## Options

```
--size a4-landscape | a4 | a5-landscape | square | letter-landscape | letter
--max-px 2400        downscale images (smaller PDF); use 3500 for professional print
--no-overview        skip the "journey" overview page after the cover
-o folder            output folder (default: <project>/_output)
--pdf-name name.pdf
```
