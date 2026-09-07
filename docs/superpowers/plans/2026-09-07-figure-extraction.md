# Figure and Table Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a rectangle drawn around a figure or table in a PDF become an image embed in the paper's Obsidian note, numbered into the same reading-order sequence as the highlighted quotes.

**Architecture:** `highlights.extract` grows a second arm that admits drawn shapes (`/Square`, `/Circle`, `/Polygon`, `/Ink`) by their `/Rect` the way it admits selections by their `/QuadPoints`, so figures and quotes share one `order` sequence and every downstream mechanism — grouping, stickiness, audit, scoring — is untouched. A new `figures.py` owns the one new concern, cropping a rect of a PDF to a deterministic PNG, and is the only module importing `pypdfium2`. The CLI joins the two: `highlights` yields a rect, `figures` turns it into an asset path.

**Tech Stack:** Python 3.14, `pdfminer.six` (annotations and text geometry), `pypdfium2` (rasterising — new), stdlib `zlib`/`struct` (PNG encoding), `typer` (CLI), `pytest`, `mypy --strict`, `ruff`.

**Spec:** `docs/superpowers/specs/2026-09-07-figure-extraction-design.md`

## Global Constraints

- **Python floor:** `>=3.14`. Use `from __future__ import annotations` in every new module, matching the existing ones.
- **One new dependency, one importer:** `pypdfium2` goes in `[project.dependencies]` and may be imported by `src/research_assistant/figures.py` **only**. `highlights.py` must not import `figures`; `figures.py` must not import `highlights`, `vault`, or `search`.
- **Line length 88.** `ruff` config at `pyproject.toml`. Do **not** run `ruff format` by hand — a `format-on-edit` hook applies it.
- **`mypy --strict` must pass** over `src/` and `tests/`.
- **Never mutate a quote.** `Highlight.text` and `Quote.text` hold verbatim page text and nothing else. A figure's `text` is `""`; its markup lives in a separate `asset` field.
- **Render constants, exact values:** 200 DPI (`RENDER_SCALE = 200 / 72`), `DRAW_ANNOTS = False`, digest truncated to 8 hex characters, rect rounded to 1 decimal place before hashing.
- **Validity rules, exact values:** figure subtypes `{"Square", "Circle", "Polygon", "Ink"}`; clipped rect must be under `0.9` of page area and at least `100.0` pt².
- **Asset location:** `papers_dir.parent / "assets" / <cite_key> /`, filenames `p{page:02d}-{digest}.png`.
- **Commits:** Conventional Commits, subject ≤ 72 chars, imperative mood.
- **Test command:** `uv run pytest` (vault-marked tests are deselected by default).

---

### Task 1: The PNG encoder

A PNG for a fixed 8-bit truecolour image is a signature plus three chunks. Writing it from the standard library keeps the byte layout ours and avoids a second binary wheel, following the precedent of `tests/fixtures/make_highlights_pdf.py`, which hand-writes a whole PDF for the same reason.

**Files:**
- Create: `src/research_assistant/figures.py`
- Test: `tests/test_figures.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `figures.encode_png(width: int, height: int, pixels: bytes) -> bytes`, where `pixels` is `width * height * 3` bytes of RGB in row-major order, top row first. Raises `figures.FigureError` if the buffer length disagrees with the dimensions.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_figures.py`:

```python
"""Cropping a region of a PDF to a deterministic asset on disk.

The encoder is a pure byte function and is tested by decoding what it wrote
back with ``zlib``: a PNG that a reader cannot invert is not a PNG.
"""

from __future__ import annotations

import struct
import zlib

import pytest

from research_assistant import figures

# --- The encoder -----------------------------------------------------------

RED = b"\xff\x00\x00"
GREEN = b"\x00\xff\x00"
BLUE = b"\x00\x00\xff"
WHITE = b"\xff\xff\xff"


def chunks(png: bytes) -> list[tuple[bytes, bytes]]:
    """Every chunk in a PNG as ``(tag, data)``, verifying each CRC."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    out: list[tuple[bytes, bytes]] = []
    index = 8
    while index < len(png):
        (length,) = struct.unpack(">I", png[index : index + 4])
        tag = png[index + 4 : index + 8]
        data = png[index + 8 : index + 8 + length]
        (crc,) = struct.unpack(">I", png[index + 8 + length : index + 12 + length])
        assert crc == zlib.crc32(tag + data) & 0xFFFFFFFF, f"bad CRC on {tag!r}"
        out.append((tag, data))
        index += 12 + length
    return out


def test_encode_png_writes_the_expected_chunk_sequence() -> None:
    png = figures.encode_png(2, 2, RED + GREEN + BLUE + WHITE)
    assert [tag for tag, _ in chunks(png)] == [b"IHDR", b"IDAT", b"IEND"]


def test_encode_png_declares_truecolour_eight_bit() -> None:
    png = figures.encode_png(2, 2, RED + GREEN + BLUE + WHITE)
    header = dict(chunks(png))[b"IHDR"]
    width, height, depth, colour, compress, filt, interlace = struct.unpack(
        ">IIBBBBB", header
    )
    assert (width, height) == (2, 2)
    # 8-bit, colour type 2 (truecolour), deflate, adaptive filtering, no interlace.
    assert (depth, colour, compress, filt, interlace) == (8, 2, 0, 0, 0)


def test_encode_png_round_trips_the_pixels() -> None:
    """Each scanline is prefixed with filter byte 0, so inverting is a slice."""
    pixels = RED + GREEN + BLUE + WHITE
    png = figures.encode_png(2, 2, pixels)
    raw = zlib.decompress(dict(chunks(png))[b"IDAT"])
    assert raw == b"\x00" + RED + GREEN + b"\x00" + BLUE + WHITE


def test_encode_png_rejects_a_buffer_that_does_not_match_the_dimensions() -> None:
    with pytest.raises(figures.FigureError, match="12 bytes"):
        figures.encode_png(2, 2, RED + GREEN)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_figures.py -v`
Expected: FAIL, collection error — `ModuleNotFoundError: No module named 'research_assistant.figures'`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/research_assistant/figures.py`:

```python
"""Crop a rectangle of a PDF page to a deterministic image on disk.

This module knows nothing about notes, quotes, headings or cite keys: it takes
a PDF, a page and a rectangle, and it returns a path. It is also the only place
``pypdfium2`` is imported, so the raster dependency stays out of the module that
owns the note format.

The determinism claimed here is specific. The PNG bytes are *not* guaranteed
identical across ``pypdfium2`` versions, and pretending otherwise would be a
promise nobody can keep. What is guaranteed is that a file already on disk is
never re-rendered, so a library upgrade cannot churn a vault.
"""

from __future__ import annotations

import struct
import zlib

# PNG colour type 2 is truecolour: three 8-bit channels, no palette, no alpha.
BIT_DEPTH = 8
COLOUR_TYPE = 2
CHANNELS = 3
SIGNATURE = b"\x89PNG\r\n\x1a\n"


class FigureError(Exception):
    """Raised when a region cannot be rendered, or an asset cannot be written."""


def _chunk(tag: bytes, data: bytes) -> bytes:
    """One PNG chunk: length, tag, data, and the CRC over tag and data."""
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def encode_png(width: int, height: int, pixels: bytes) -> bytes:
    """A truecolour PNG holding ``pixels``, which is RGB, top row first.

    Every scanline is prefixed with filter byte 0. Filtering would compress
    better, but the file is written once and never read back by this tool, and
    an unfiltered image is one a reader can invert with a slice.
    """
    expected = width * height * CHANNELS
    if len(pixels) != expected:
        raise FigureError(
            f"expected {expected} bytes for {width}x{height} RGB, "
            f"got {len(pixels)} bytes"
        )
    stride = width * CHANNELS
    raw = b"".join(
        b"\x00" + pixels[y * stride : (y + 1) * stride] for y in range(height)
    )
    header = struct.pack(
        ">IIBBBBB", width, height, BIT_DEPTH, COLOUR_TYPE, 0, 0, 0
    )
    return (
        SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_figures.py -v`
Expected: 4 passed.

- [ ] **Step 5: Typecheck and lint**

Run: `uv run mypy src/ tests/ && uv run ruff check src/ tests/`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/research_assistant/figures.py tests/test_figures.py
git commit -m "feat(figures): write a truecolour PNG from the standard library"
```

---

### Task 2: The asset name

The filename is a cache key. Deriving it from every input to the render is what makes a re-run rewrite nothing, and makes a changed scale constant rename files rather than leave stale content behind an unchanged name.

**Files:**
- Modify: `src/research_assistant/figures.py`
- Test: `tests/test_figures.py`

**Interfaces:**
- Consumes: `figures.FigureError` from Task 1.
- Produces:
  - `figures.RENDER_SCALE: float` (`200 / 72`)
  - `figures.DRAW_ANNOTS: bool` (`False`)
  - `figures.Rect = tuple[float, float, float, float]` (a type alias, `x0, y0, x1, y1`)
  - `figures.asset_name(page: int, rect: Rect) -> str`, returning e.g. `"p07-3f9a1c2b.png"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_figures.py`:

```python
# --- The asset name --------------------------------------------------------

SQUARE: figures.Rect = (83.45156, 482.8865, 517.0561, 717.8801)


def test_asset_name_has_the_page_and_a_digest() -> None:
    name = figures.asset_name(7, SQUARE)
    assert name.startswith("p07-")
    assert name.endswith(".png")
    assert len(name) == len("p07-") + 8 + len(".png")


def test_asset_name_is_stable_for_the_same_box() -> None:
    assert figures.asset_name(7, SQUARE) == figures.asset_name(7, SQUARE)


def test_asset_name_ignores_movement_below_a_tenth_of_a_point() -> None:
    """0.1pt is 0.035mm: nothing you could draw distinguishes these two boxes."""
    nudged = (SQUARE[0] + 0.001, SQUARE[1], SQUARE[2], SQUARE[3])
    assert figures.asset_name(7, nudged) == figures.asset_name(7, SQUARE)


def test_asset_name_changes_when_the_box_really_moves() -> None:
    moved = (SQUARE[0] + 5.0, SQUARE[1], SQUARE[2], SQUARE[3])
    assert figures.asset_name(7, moved) != figures.asset_name(7, SQUARE)


def test_asset_name_changes_with_the_page() -> None:
    assert figures.asset_name(8, SQUARE) != figures.asset_name(7, SQUARE)


def test_asset_name_changes_with_the_render_scale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scale is an input to the render, so it is an input to the name."""
    before = figures.asset_name(7, SQUARE)
    monkeypatch.setattr(figures, "RENDER_SCALE", figures.RENDER_SCALE * 2)
    assert figures.asset_name(7, SQUARE) != before
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_figures.py -k asset_name -v`
Expected: FAIL — `AttributeError: module 'research_assistant.figures' has no attribute 'asset_name'`.

- [ ] **Step 3: Write the minimal implementation**

Add to `src/research_assistant/figures.py`, after the existing constants:

```python
import hashlib

# 200 DPI over the PDF's 72. At 150 the serifs of a figure caption go soft; 300
# roughly doubles the bytes for a difference Obsidian's preview does not show.
RENDER_SCALE = 200 / 72

# The crop is the paper, not the paper with your own box drawn across it.
DRAW_ANNOTS = False

# 0.1pt is 0.035mm. Rounding first stabilises float formatting, and merges only
# boxes no hand could tell apart.
RECT_PRECISION = 1

# Eight hex characters, inside a namespace of one paper's figures.
DIGEST_LENGTH = 8

type Rect = tuple[float, float, float, float]
```

and this function:

```python
def asset_name(page: int, rect: Rect) -> str:
    """The filename a page and a rectangle always resolve to.

    Every input to the render goes into the digest, not just the geometry: if
    the scale constant is ever changed, every asset gets a new name and is
    re-rendered, rather than stale content hiding behind a name that still
    looks current.
    """
    seed = "|".join(
        [
            str(page),
            *(f"{value:.{RECT_PRECISION}f}" for value in rect),
            f"{RENDER_SCALE:.6f}",
            str(DRAW_ANNOTS),
        ]
    )
    digest = hashlib.blake2b(seed.encode("utf-8"), digest_size=16).hexdigest()
    return f"p{page:02d}-{digest[:DIGEST_LENGTH]}.png"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_figures.py -v`
Expected: 10 passed.

- [ ] **Step 5: Typecheck and lint**

Run: `uv run mypy src/ tests/ && uv run ruff check src/ tests/`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/research_assistant/figures.py tests/test_figures.py
git commit -m "feat(figures): name an asset for every input to its render"
```

---

### Task 3: Rendering a region

**Files:**
- Modify: `pyproject.toml` (add `pypdfium2` to `[project.dependencies]`)
- Modify: `src/research_assistant/figures.py`
- Test: `tests/test_figures.py`

**Interfaces:**
- Consumes: `figures.encode_png`, `figures.asset_name`, `figures.Rect`, `figures.FigureError`.
- Produces: `figures.render(pdf: Path, page: int, rect: Rect, *, into: Path) -> Path`. Creates `into` if absent, writes `into / asset_name(page, rect)` **only if that file does not already exist**, and returns the path either way.

- [ ] **Step 1: Add the dependency**

`pypdfium2` is not reachable from the sandbox — only `github.com` is on the network allowlist — so this command needs to run outside it or with an exception granted:

```bash
uv add pypdfium2
```

Confirm `pyproject.toml`'s `[project.dependencies]` now lists `pypdfium2` alongside `httpx`, `pdfminer-six`, `pyyaml` and `typer`.

- [ ] **Step 2: Confirm the bitmap layout empirically**

The buffer's channel order and row stride are the two things this task can get silently wrong, and a stride assumption that is wrong by a few bytes produces a sheared image rather than an error. Establish them before writing the code:

```bash
uv run python -c "
import pypdfium2 as pdfium
doc = pdfium.PdfDocument('tests/fixtures/probe.pdf') if False else None
print('pypdfium2', pdfium.V_PYPDFIUM2)
"
```

Then, against a real PDF you have to hand:

```bash
uv run python -c "
import pypdfium2 as pdfium
doc = pdfium.PdfDocument('<path to any pdf>')
bmp = doc[0].render(scale=2, draw_annots=False, rev_byteorder=True)
print('mode', bmp.mode, 'w', bmp.width, 'h', bmp.height, 'stride', bmp.stride)
print('padded' if bmp.stride != bmp.width * len(bmp.mode) else 'tight')
"
```

Expected: `mode` is `BGRA`/`RGBA`/`RGB`. The implementation below handles a 4-channel mode by deleting the alpha byte and a padded stride by slicing each row; if the mode printed is `BGRA` even with `rev_byteorder=True`, the channel order must be reversed too — write that down before Step 4 rather than discovering it from a blue-tinted image.

- [ ] **Step 3: Write the failing tests**

Append to `tests/test_figures.py`:

First extend the module's import block at the top of the file — `ruff` will
reject imports part-way down:

```python
from pathlib import Path

from fixtures.make_highlights_pdf import write
```

Then append the tests:

```python
# --- Rendering -------------------------------------------------------------


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    return write(tmp_path / "render.pdf")


def test_render_writes_the_asset_under_its_own_name(pdf: Path, tmp_path: Path) -> None:
    into = tmp_path / "assets" / "yen2023soilpowered"
    box: figures.Rect = (100.0, 600.0, 400.0, 750.0)
    path = figures.render(pdf, 1, box, into=into)
    assert path == into / figures.asset_name(1, box)
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_creates_the_folder(pdf: Path, tmp_path: Path) -> None:
    into = tmp_path / "assets" / "yen2023soilpowered"
    assert not into.exists()
    figures.render(pdf, 1, (100.0, 600.0, 400.0, 750.0), into=into)
    assert into.is_dir()


def test_render_scales_the_crop_by_the_render_scale(pdf: Path, tmp_path: Path) -> None:
    """A 300x150pt box at 200 DPI is about 833x417 pixels."""
    path = figures.render(
        pdf, 1, (100.0, 600.0, 400.0, 750.0), into=tmp_path / "a"
    )
    width, height = struct.unpack(">II", dict(chunks(path.read_bytes()))[b"IHDR"][:8])
    assert abs(width - round(300 * figures.RENDER_SCALE)) <= 2
    assert abs(height - round(150 * figures.RENDER_SCALE)) <= 2


def test_render_does_not_rewrite_an_asset_that_exists(
    pdf: Path, tmp_path: Path
) -> None:
    """The guarantee the design rests on: a second run rasterises nothing."""
    into = tmp_path / "a"
    box: figures.Rect = (100.0, 600.0, 400.0, 750.0)
    path = figures.render(pdf, 1, box, into=into)
    path.write_bytes(b"sentinel")
    assert figures.render(pdf, 1, box, into=into) == path
    assert path.read_bytes() == b"sentinel"


def test_render_reports_a_page_the_pdf_does_not_have(pdf: Path, tmp_path: Path) -> None:
    with pytest.raises(figures.FigureError, match="page 9"):
        figures.render(pdf, 9, (100.0, 600.0, 400.0, 750.0), into=tmp_path / "a")
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_figures.py -k render -v`
Expected: FAIL — `AttributeError: module 'research_assistant.figures' has no attribute 'render'`.

- [ ] **Step 5: Write the minimal implementation**

Add to `src/research_assistant/figures.py`:

```python
from typing import TYPE_CHECKING

import pypdfium2 as pdfium

if TYPE_CHECKING:
    from pathlib import Path
```

and:

```python
def _rgb(bitmap: pdfium.PdfBitmap) -> bytes:
    """The bitmap's pixels as tight RGB, top row first.

    Two things pdfium does that a naive read gets wrong: rows may be padded to
    a stride wider than the pixels, and the buffer may carry a fourth alpha
    channel. Both are handled here so the encoder only ever sees RGB.
    """
    channels = len(bitmap.mode)
    if channels not in (3, 4):
        raise FigureError(f"unexpected bitmap mode {bitmap.mode!r}")
    buffer = bytes(bitmap.buffer)
    out = bytearray()
    for y in range(bitmap.height):
        start = y * bitmap.stride
        row = bytearray(buffer[start : start + bitmap.width * channels])
        if channels == 4:
            # RGBA: drop every fourth byte.
            del row[3::4]
        out += row
    return bytes(out)


def render(pdf: Path, page: int, rect: Rect, *, into: Path) -> Path:
    """The asset for one rectangle of one page, rendering it only if absent.

    ``rect`` is in PDF user space with the origin at the bottom left, which is
    what an annotation's ``/Rect`` is in. pdfium crops by how much to cut from
    each edge instead, so the four values are converted against the page size.
    """
    target = into / asset_name(page, rect)
    if target.exists():
        return target
    try:
        document = pdfium.PdfDocument(pdf)
        if not 1 <= page <= len(document):
            raise FigureError(f"{pdf}: page {page} of {len(document)}")
        loaded = document[page - 1]
        width, height = loaded.get_size()
        x0, y0, x1, y1 = rect
        bitmap = loaded.render(
            scale=RENDER_SCALE,
            draw_annots=DRAW_ANNOTS,
            rev_byteorder=True,
            crop=(x0, y0, width - x1, height - y1),
        )
        png = encode_png(bitmap.width, bitmap.height, _rgb(bitmap))
    except FigureError:
        raise
    except Exception as exc:
        raise FigureError(f"{pdf}: {exc}") from exc
    into.mkdir(parents=True, exist_ok=True)
    target.write_bytes(png)
    return target
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_figures.py -v`
Expected: 15 passed.

- [ ] **Step 7: Eyeball one crop against the real corpus**

Automated tests cannot tell a correct image from a sheared or blue-tinted one. Render the box this design was probed from and look at it:

```bash
uv run python -c "
from pathlib import Path
from research_assistant import figures
p = Path.home() / 'Obsidian/School/Y4S1/CP4101 B.Comp. Dissertation/pdfs/garca2020iotbased.pdf'
print(figures.render(p, 7, (83.45156, 482.8865, 517.0561, 717.8801), into=Path('/tmp/fig')))
"
```

Expected: a pie chart captioned "Figure 6. Number of monitored parameters…", correct colours, **no yellow annotation border** (that is `draw_annots=False` working). A blue-and-orange-swapped image means the channel order from Step 2 needs reversing.

- [ ] **Step 8: Typecheck and lint**

Run: `uv run mypy src/ tests/ && uv run ruff check src/ tests/`
Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock src/research_assistant/figures.py tests/test_figures.py
git commit -m "feat(figures): crop a page rectangle to a PNG with pdfium"
```

---

### Task 4: Admitting a drawn shape as a figure

**Files:**
- Modify: `tests/fixtures/make_highlights_pdf.py`
- Modify: `src/research_assistant/highlights.py` (the `Highlight` dataclass; the annotation loop in `extract`, around `highlights.py:250`)
- Test: `tests/test_highlights.py`

**Interfaces:**
- Consumes: nothing from earlier tasks — `highlights.py` must **not** import `figures`.
- Produces:
  - `highlights.KIND_QUOTE: str` (`"quote"`), `highlights.KIND_FIGURE: str` (`"figure"`)
  - `highlights.FIGURE_SUBTYPES: frozenset[str]`
  - `Highlight` gains `kind: str = KIND_QUOTE` and `rect: tuple[float, float, float, float] | None = None`
  - `make_highlights_pdf.write(path: Path, *, with_figure: bool = False) -> Path` and `make_highlights_pdf.FIGURE_RECT: tuple[float, float, float, float]`

- [ ] **Step 1: Add a drawn shape to the fixture PDF**

The square is opt-in so that every existing test keeps the two-highlight PDF it was written against. It sits **above** the page-1 highlight, so it must sort first: an implementation that appends figures rather than interleaving them fails.

In `tests/fixtures/make_highlights_pdf.py`, add after the `FLT_MAX` constant:

```python
# A rectangle drawn round a figure, as Preview, Skim and Acrobat all write one.
# It sits above the page-1 highlight (whose quads top out near y=710), so
# reading order must place it first.
FIGURE_RECT = (100.0, 730.0, 400.0, 780.0)
```

then, in `build`, change the signature and add two annotations:

```python
def build(
    *,
    with_figure: bool = False,
    figure_rect: tuple[float, float, float, float] = FIGURE_RECT,
) -> bytes:
    """The whole PDF: two pages, two selection highlights, one free-draw.

    ``with_figure`` adds two drawn shapes to page 1: a ``/Square`` at
    ``figure_rect``, and a second one carrying the whole-page ``FLT_MAX``
    rectangle a free-draw writes, which the area ceiling must reject. Without
    the second, nothing distinguishes an area test from a finiteness test.
    """
```

Inside `build`, beside the existing `free_draw` definition:

```python
    def _square(rect: tuple[float, float, float, float]) -> str:
        x0, y0, x1, y1 = rect
        return (
            f"<</Type/Annot/Subtype/Square"
            f"/Rect [{x0:g} {y0:g} {x1:g} {y1:g}]/C [1 1 0]>>"
        )

    drawn = (
        f" {_square(figure_rect)}"
        f" <</Type/Annot/Subtype/Square/Rect [0 0 {FLT_MAX} {FLT_MAX}]/C [1 1 0]>>"
        if with_figure
        else ""
    )
    page_one_annots = f"{_highlight(page_one_quads)}{drawn}"
```

and use `page_one_annots` in place of `{_highlight(page_one_quads)}` in the page-1 object's `/Annots` array. Finally:

```python
def write(
    path: Path,
    *,
    with_figure: bool = False,
    figure_rect: tuple[float, float, float, float] = FIGURE_RECT,
) -> Path:
    """Write the fixture PDF to ``path`` and return it."""
    path.write_bytes(build(with_figure=with_figure, figure_rect=figure_rect))
    return path
```

Note that `test_a_stray_click_is_not_a_figure` passes a `figure_rect` of
5×5pt, so the only shape that survives in that PDF is the `FLT_MAX` one, which
the ceiling also rejects — hence two quotes and no figures.

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_highlights.py`, after the existing tier-2 geometry tests. Extend the import at the top to `from fixtures.make_highlights_pdf import FIGURE_RECT, PAGE_ONE, PAGE_TWO, write`.

```python
@pytest.fixture
def figure_pdf(tmp_path: Path) -> Path:
    """The same PDF, plus a ``/Square`` above the page-1 highlight."""
    return write(tmp_path / "figures.pdf", with_figure=True)


def test_extract_admits_a_drawn_rectangle(figure_pdf: Path) -> None:
    found, _ = highlights.extract(figure_pdf)
    figures = [h for h in found if h.kind == highlights.KIND_FIGURE]
    assert [(f.page, f.rect) for f in figures] == [(1, FIGURE_RECT)]


def test_a_figure_carries_no_text(figure_pdf: Path) -> None:
    """``text`` means what the page says, and a rectangle says nothing."""
    found, _ = highlights.extract(figure_pdf)
    assert [h.text for h in found if h.kind == highlights.KIND_FIGURE] == [""]


def test_a_figure_is_numbered_by_where_it_sits_on_the_page(
    figure_pdf: Path,
) -> None:
    """The square is above the page-1 highlight, so it takes order 1."""
    found, _ = highlights.extract(figure_pdf)
    assert [(h.order, h.page, h.kind) for h in found] == [
        (1, 1, highlights.KIND_FIGURE),
        (2, 1, highlights.KIND_QUOTE),
        (3, 2, highlights.KIND_QUOTE),
    ]


def test_a_drawn_shape_with_a_whole_page_rect_is_skipped(figure_pdf: Path) -> None:
    """The area ceiling, which is the only thing that catches a free-draw box.

    ``FLT_MAX`` is a finite float and clips to exactly the page, so a
    finiteness check would admit it and crop the whole page as a "figure".
    """
    found, skipped = highlights.extract(figure_pdf)
    assert [(s.page, s.reason) for s in skipped] == [
        (1, highlights.FREE_DRAW),  # the /Square carrying FLT_MAX
        (2, highlights.FREE_DRAW),  # the quad-less /Highlight
    ]
    assert all(h.rect != (0.0, 0.0, 612.0, 792.0) for h in found)


def test_a_stray_click_is_not_a_figure(tmp_path: Path) -> None:
    """Below 100 pt^2 there is nothing worth cropping."""
    tiny = write(tmp_path / "tiny.pdf", with_figure=True, figure_rect=(10, 10, 15, 15))
    found, _ = highlights.extract(tiny)
    assert [h.kind for h in found] == [highlights.KIND_QUOTE] * 2


def test_a_quote_still_reports_its_kind(fixture_pdf: Path) -> None:
    found, _ = highlights.extract(fixture_pdf)
    assert {h.kind for h in found} == {highlights.KIND_QUOTE}
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_highlights.py -k "figure or kind" -v`
Expected: FAIL — `AttributeError: module 'research_assistant.highlights' has no attribute 'KIND_FIGURE'`.

- [ ] **Step 4: Write the implementation**

In `src/research_assistant/highlights.py`, add beside the existing `FREE_DRAW`/`NO_TEXT` constants:

```python
KIND_QUOTE = "quote"
KIND_FIGURE = "figure"

# The shapes you draw, as against the selection you drag. Preview writes
# `/Square`, and the others are what the same gesture becomes in other readers.
FIGURE_SUBTYPES = frozenset({"Square", "Circle", "Polygon", "Ink"})

# A free-draw annotation records no geometry: its `/Rect` is
# `[0 0 FLT_MAX FLT_MAX]`, which is *finite* and clips to exactly the page. So
# the discriminator has to be area, not a finiteness check.
PAGE_AREA_CEILING = 0.9

# 100 pt^2 is a 10x10pt box: below this it is a stray click, not a figure.
MIN_FIGURE_AREA = 100.0
```

Extend the dataclass:

```python
@dataclass(frozen=True, slots=True)
class Highlight:
    """One highlighted passage or one boxed figure, as the PDF has it."""

    page: int
    text: str
    order: int
    kind: str = KIND_QUOTE
    rect: tuple[float, float, float, float] | None = None
```

Add the geometry helper, beside `_quads`:

```python
def _rect(annot: Mapping[str, Any], box: Sequence[float]) -> tuple[float, ...] | None:
    """A drawn shape's ``/Rect``, normalised and clipped to the page.

    ``None`` when the rectangle is unusable: inverted to nothing by the clip,
    a stray click, or the whole-page rectangle a free-draw writes.
    """
    values = resolve1(annot.get("Rect"))
    if not isinstance(values, list) or len(values) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in values)
    except (TypeError, ValueError):
        return None
    px0, py0, px1, py1 = (float(v) for v in box)
    left, right = max(min(x0, x1), px0), min(max(x0, x1), px1)
    bottom, top = max(min(y0, y1), py0), min(max(y0, y1), py1)
    if right <= left or top <= bottom:
        return None
    area = (right - left) * (top - bottom)
    page_area = (px1 - px0) * (py1 - py0)
    if area < MIN_FIGURE_AREA or area >= PAGE_AREA_CEILING * page_area:
        return None
    # Translated to the mediabox origin, the same shift pdfminer applies to the
    # characters. A page whose mediabox does not start at (0, 0) is rare but
    # real, and the renderer measures its crop from the page's own edge.
    return (left - px0, bottom - py0, right - px0, top - py0)
```

The returned rectangle is therefore **mediabox-relative**, which is the
coordinate space `figures.render` crops in. `figures.py` never learns what a
mediabox is; the translation happens here, next to the only code that has the
page object to hand.

In `extract`, replace the early `continue` on a non-highlight subtype with a
figure arm. The existing line

```python
                    if _name(resolved.get("Subtype")) != "Highlight":
                        continue
```

becomes:

```python
                    subtype = _name(resolved.get("Subtype"))
                    if subtype in FIGURE_SUBTYPES:
                        rect = _rect(resolved, page.mediabox)
                        if rect is None:
                            skipped.append(Skipped(number, FREE_DRAW))
                            continue
                        found.append(
                            (
                                _order_key(number, _as_quad(rect)),
                                number,
                                "",
                                KIND_FIGURE,
                                rect,
                            )
                        )
                        continue
                    if subtype != "Highlight":
                        continue
```

`_order_key` reads a quad's tops and lefts, so give it one built from the rect,
beside `_rect`:

```python
def _as_quad(rect: tuple[float, ...]) -> tuple[float, ...]:
    """A rectangle in the 8-number quad form ``_order_key`` reads."""
    x0, y0, x1, y1 = rect
    return (x0, y1, x1, y1, x0, y0, x1, y0)
```

The `found` list now carries five-tuples, so widen its annotation and the two
places that build from it. The quote arm's append becomes:

```python
                    found.append(
                        (_order_key(number, quads[0]), number, text, KIND_QUOTE, None)
                    )
```

the declaration becomes:

```python
    found: list[
        tuple[
            tuple[int, float, float],
            int,
            str,
            str,
            tuple[float, ...] | None,
        ]
    ] = []
```

and the final comprehension becomes:

```python
    highlights = [
        Highlight(page=page, text=text, order=index, kind=kind, rect=rect)
        for index, (_, page, text, kind, rect) in enumerate(found, start=1)
    ]
```

Note `rect` is typed `tuple[float, ...] | None` inside `extract` but the
dataclass field is a 4-tuple; annotate `_rect`'s return as
`tuple[float, float, float, float] | None` and keep that type through, so
`mypy --strict` has no cast to complain about.

- [ ] **Step 5: Run the whole highlights suite**

Run: `uv run pytest tests/test_highlights.py -v`
Expected: all pass — the new five, plus every pre-existing test unchanged, because the fixture's square is opt-in.

- [ ] **Step 6: Typecheck and lint**

Run: `uv run mypy src/ tests/ && uv run ruff check src/ tests/`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/research_assistant/highlights.py tests/
git commit -m "feat(highlights): admit a drawn rectangle as a figure"
```

---

### Task 5: A figure as a bullet in the note

**Files:**
- Modify: `src/research_assistant/highlights.py` (`Quote`, `_item`, `split_notes`, `group_quotes`, `grouping_of`)
- Test: `tests/test_highlights.py`

**Interfaces:**
- Consumes: `highlights.KIND_FIGURE`, `Highlight.kind`, `Highlight.rect` from Task 4.
- Produces:
  - `Highlight` gains `asset: str | None = None` (a vault-relative path, filled in by the CLI in Task 6)
  - `Quote` gains `asset: str | None = None`
  - `highlights.EMBED_PREFIX: str` (`"!"`), and `_item` renders `- ![[<asset>]] (<page link>)` when `asset` is set
  - `split_notes` returns a `Quote` with `asset` set and `text=""` for an embed bullet
  - `grouping_of` keys on `(page, text, asset)`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_highlights.py`, in the note-region section:

```python
ASSET = "assets/yen2023soilpowered/p07-3f9a1c2b.png"


def figure_quote(page: int, asset: str) -> highlights.Quote:
    return highlights.Quote(page=page, text="", asset=asset)


def test_a_figure_renders_as_an_embed_bullet() -> None:
    section = render("", [("Cell design", [figure_quote(7, ASSET)])])
    assert f"- ![[{ASSET}]] ([[{PDF_NAME}#page=7|p. 7]])" in section


def test_a_figure_round_trips_through_render_and_split() -> None:
    groups = [("Cell design", [figure_quote(7, ASSET)])]
    _, parsed = highlights.split_notes(render("", groups))
    assert parsed == [(name, list(qs)) for name, qs in groups]


def test_a_figure_and_a_quote_share_one_group() -> None:
    groups = [
        (
            "Cell design",
            [figure_quote(7, ASSET), *quotes((7, "produce as much as 200 µW"))],
        )
    ]
    _, parsed = highlights.split_notes(render("", groups))
    assert parsed == [(name, list(qs)) for name, qs in groups]


def test_an_embed_is_not_wrapped_across_lines() -> None:
    """The asset path is one token; splitting it would break the link."""
    long_asset = "assets/a-very-long-cite-key-indeed-2020/p07-3f9a1c2b.png"
    section = render("", [("G", [figure_quote(7, long_asset)])])
    assert f"![[{long_asset}]]" in section


def test_group_quotes_places_a_figure_by_its_order() -> None:
    found = [
        highlights.Highlight(
            page=1, text="", order=1, kind=highlights.KIND_FIGURE, asset=ASSET
        ),
        highlights.Highlight(page=1, text="a quote", order=2),
    ]
    groups = highlights.group_quotes(found, {"G": [1, 2]})
    assert groups == [
        ("G", [highlights.Quote(page=1, text="", asset=ASSET),
               highlights.Quote(page=1, text="a quote")])
    ]


def test_grouping_of_recovers_a_figure() -> None:
    found = [
        highlights.Highlight(
            page=7, text="", order=1, kind=highlights.KIND_FIGURE, asset=ASSET
        )
    ]
    grouping, unresolved = highlights.grouping_of(
        found, [("Cell design", [figure_quote(7, ASSET)])]
    )
    assert grouping == {"Cell design": [1]}
    assert unresolved == []


def test_a_figure_whose_asset_moved_is_unresolved() -> None:
    """A nudged annotation renames its asset, and that is drift, not a repair."""
    found = [
        highlights.Highlight(
            page=7, text="", order=1, kind=highlights.KIND_FIGURE, asset=ASSET
        )
    ]
    stale = figure_quote(7, "assets/yen2023soilpowered/p07-deadbeef.png")
    grouping, unresolved = highlights.grouping_of(found, [("Cell design", [stale])])
    assert grouping == {"Cell design": []}
    assert unresolved == [stale]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_highlights.py -k "figure or embed" -v`
Expected: FAIL — `TypeError: Quote.__init__() got an unexpected keyword argument 'asset'`.

- [ ] **Step 3: Write the implementation**

Extend both dataclasses in `src/research_assistant/highlights.py`:

```python
@dataclass(frozen=True, slots=True)
class Quote:
    """One bullet as it appears in a note: the text or embed, and its page."""

    page: int
    text: str
    asset: str | None = None
```

and add `asset: str | None = None` as the last field of `Highlight` (after
`rect`), so the CLI can fill it in with `dataclasses.replace`.

Add the embed constants beside `BULLET`:

```python
# An Obsidian embed, as one unbreakable token: an asset path holds no spaces,
# so unlike a page link it needs no placeholder while wrapping.
EMBED_OPEN = "![["
EMBED_CLOSE = "]]"
```

`_item` picks what the bullet says:

```python
def _item(quote: Quote, *, pdf_name: str) -> str:
    """One bullet: the quote or the embed, then its page link, wrapped."""
    body = (
        f"{EMBED_OPEN}{quote.asset}{EMBED_CLOSE}"
        if quote.asset is not None
        else quote.text
    )
    link = f"({page_link(pdf_name, quote.page)})"
    wrapped = textwrap.wrap(
        f"{body} {link.replace(' ', _GUARD)}",
        width=WRAP_WIDTH,
        initial_indent=BULLET,
        subsequent_indent=CONTINUATION,
        break_long_words=False,
        # A break on a hyphen would gain a space when the audit unwraps the
        # item, and the comparison is exact string equality.
        break_on_hyphens=False,
    )
    return "\n".join(wrapped).replace(_GUARD, " ")
```

The `break_long_words=False` already there is what keeps a long asset path on
one line, so the embed needs no `_GUARD` treatment of its own: unlike a page
link, a path contains no spaces to protect.

`split_notes`'s `flush` recognises an embed. Replace the append inside `flush`:

```python
        if text and match is not None:
            page = int(match.group(1))
            if text.startswith(EMBED_OPEN) and text.endswith(EMBED_CLOSE):
                asset = text[len(EMBED_OPEN) : -len(EMBED_CLOSE)]
                groups[-1][1].append(Quote(page=page, text="", asset=asset))
            else:
                groups[-1][1].append(Quote(page=page, text=text))
```

`group_quotes`'s final comprehension carries the asset through:

```python
                Quote(
                    page=by_order[order].page,
                    text=by_order[order].text,
                    asset=by_order[order].asset,
                )
```

and `grouping_of` keys on all three fields — two lines in its body, shown here
in place:

```python
    by_key = {(h.page, h.text, h.asset): h.order for h in highlights}
    grouping: dict[str, list[int]] = {}
    unresolved: list[Quote] = []
    for heading, quotes in groups:
        orders = grouping.setdefault(heading, [])
        for quote in quotes:
            order = by_key.get((quote.page, quote.text, quote.asset))
            if order is None:
                unresolved.append(quote)
            else:
                orders.append(order)
    return grouping, unresolved
```

- [ ] **Step 4: Run the whole highlights suite**

Run: `uv run pytest tests/test_highlights.py -v`
Expected: all pass. `test_notes_round_trip_through_render_and_split` and the other pre-existing round-trip tests still pass because `asset` defaults to `None`.

- [ ] **Step 5: Typecheck and lint**

Run: `uv run mypy src/ tests/ && uv run ruff check src/ tests/`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/research_assistant/highlights.py tests/test_highlights.py
git commit -m "feat(highlights): render and parse a figure as an embed bullet"
```

---

### Task 6: Wiring the CLI

**Files:**
- Modify: `src/research_assistant/vault.py` (add `ASSETS_DIRNAME`, beside `PDFS_DIRNAME` at `vault.py:44`)
- Modify: `src/research_assistant/search.py` (add `assets_dir_for`, beside `pdfs_dir_for` at `search.py:324`)
- Modify: `src/research_assistant/cli.py` (`_extract` at `cli.py:2326`; `_placed` at `cli.py:2364`; the JSON output in `highlight`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `figures.render`, `figures.Rect` (Task 3); `highlights.KIND_FIGURE`, `Highlight.rect`, `Highlight.asset` (Tasks 4–5).
- Produces:
  - `vault.ASSETS_DIRNAME: str` (`"assets"`)
  - `search.assets_dir_for(papers_dir: Path) -> Path`
  - `cli._extract(record: search.Record, *, papers_dir: Path) -> list[highlights.Highlight]`
  - The `highlights <key>` JSON gains `"kind"` and `"asset"` on every item.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py` has no `conftest.py` by convention and builds each vault
inline, as `test_tidy_adopts_a_hand_saved_pdf_only_when_the_note_claims_none`
at `tests/test_cli.py:313` does. Follow that shape. Add `import json` and
`from fixtures.make_highlights_pdf import write as write_pdf` to the import
block at the top of the file, then append:

```python
KEY = "yen2023soilpowered"


def annotated_vault(tmp_path: Path) -> tuple[Path, Path]:
    """A one-note vault whose PDF carries two highlights and one boxed figure."""
    papers = tmp_path / "papers"
    note = write_note(papers, "Soil-powered", key=KEY, pdf=f"{KEY}.pdf")
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    write_pdf(pdfs / f"{KEY}.pdf", with_figure=True)
    return papers, note


def extracted(papers: Path) -> list[dict[str, Any]]:
    result = runner.invoke(cli.app, ["--papers-dir", str(papers), "highlights", KEY])
    assert result.exit_code == 0, result.stdout
    loaded: list[dict[str, Any]] = json.loads(result.stdout)
    return loaded


def test_highlights_json_reports_kind_and_asset(tmp_path: Path) -> None:
    """A figure arrives with an asset and no text; a quote, the reverse."""
    papers, _ = annotated_vault(tmp_path)

    items = extracted(papers)

    assert [i["kind"] for i in items] == ["figure", "quote", "quote"]
    assert items[0]["text"] == ""
    assert items[0]["asset"].startswith(f"assets/{KEY}/p01-")
    assert items[1]["asset"] is None


def test_highlights_writes_the_asset_into_the_vault(tmp_path: Path) -> None:
    papers, _ = annotated_vault(tmp_path)

    extracted(papers)

    written = list((tmp_path / "assets" / KEY).glob("*.png"))
    assert len(written) == 1
    assert written[0].read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_applying_a_grouping_embeds_the_figure(tmp_path: Path) -> None:
    papers, note = annotated_vault(tmp_path)
    items = extracted(papers)
    grouping = tmp_path / "groups.json"
    grouping.write_text(
        json.dumps({"Cell design": [i["order"] for i in items]}), encoding="utf-8"
    )

    result = runner.invoke(
        cli.app,
        ["--papers-dir", str(papers), "highlights", KEY, "--apply", str(grouping)],
    )

    assert result.exit_code == 0, result.stdout
    body = note.read_text(encoding="utf-8")
    assert f"- ![[{items[0]['asset']}]] ([[{KEY}.pdf#page=1|p. 1]])" in body


def test_an_applied_figure_comes_back_carrying_its_heading(tmp_path: Path) -> None:
    """Stickiness works on a figure, so a re-run only has to place new items."""
    papers, _ = annotated_vault(tmp_path)
    items = extracted(papers)
    grouping = tmp_path / "groups.json"
    grouping.write_text(
        json.dumps({"Cell design": [i["order"] for i in items]}), encoding="utf-8"
    )
    runner.invoke(
        cli.app,
        ["--papers-dir", str(papers), "highlights", KEY, "--apply", str(grouping)],
    )

    assert [i["group"] for i in extracted(papers)] == ["Cell design"] * 3


def test_a_second_run_rewrites_neither_the_note_nor_the_assets(
    tmp_path: Path,
) -> None:
    """Idempotence end to end: no new bytes, no renamed files."""
    papers, note = annotated_vault(tmp_path)
    items = extracted(papers)
    grouping = tmp_path / "groups.json"
    grouping.write_text(
        json.dumps({"Cell design": [i["order"] for i in items]}), encoding="utf-8"
    )
    apply = ["--papers-dir", str(papers), "highlights", KEY, "--apply", str(grouping)]
    runner.invoke(cli.app, apply)
    assets = tmp_path / "assets" / KEY
    before = note.read_text(encoding="utf-8")
    names = {p.name: p.read_bytes() for p in assets.iterdir()}

    runner.invoke(cli.app, apply)

    assert note.read_text(encoding="utf-8") == before
    assert {p.name: p.read_bytes() for p in assets.iterdir()} == names


def test_a_pdf_with_no_drawn_box_creates_no_assets_folder(tmp_path: Path) -> None:
    """A vault nobody has boxed anything in is untouched by this feature."""
    papers = tmp_path / "papers"
    write_note(papers, "Soil-powered", key=KEY, pdf=f"{KEY}.pdf")
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    write_pdf(pdfs / f"{KEY}.pdf")

    runner.invoke(cli.app, ["--papers-dir", str(papers), "highlights", KEY])

    assert not (tmp_path / "assets").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "kind or asset or figure" -v`
Expected: FAIL — the JSON has no `"kind"` key.

- [ ] **Step 3: Add the vault constant and the resolver**

In `src/research_assistant/vault.py`, beside `PDFS_DIRNAME`:

```python
ASSETS_DIRNAME = "assets"
```

In `src/research_assistant/search.py`, beside `pdfs_dir_for`:

```python
def assets_dir_for(papers_dir: Path) -> Path:
    """Where cropped figures sit: a sibling of the papers folder."""
    return papers_dir.parent / vault.ASSETS_DIRNAME
```

- [ ] **Step 4: Fill in each figure's asset in `_extract`**

In `src/research_assistant/cli.py`, add the import of `figures` alongside
`highlights`, and rewrite `_extract`:

```python
def _extract(
    record: search.Record, *, papers_dir: Path
) -> list[highlights.Highlight]:
    """Read one note's PDF, rendering any boxed figure and reporting the rest.

    The join between the two modules. ``highlights`` yields a figure's
    rectangle and knows nothing about rasterising; ``figures`` turns a
    rectangle into a file and knows nothing about notes.
    """
    pdf = _pdf_of(record)
    try:
        found, skipped = highlights.extract(pdf)
    except highlights.HighlightError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    root = search.assets_dir_for(papers_dir)
    into = root / record.cite_key
    resolved: list[highlights.Highlight] = []
    for item in found:
        if item.kind != highlights.KIND_FIGURE or item.rect is None:
            resolved.append(item)
            continue
        try:
            path = figures.render(pdf, item.page, item.rect, into=into)
        except figures.FigureError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc
        # Vault-relative, because assets nest one folder per paper and a bare
        # filename would lean on Obsidian's shortest-unique-path resolution.
        asset = f"{vault.ASSETS_DIRNAME}/{record.cite_key}/{path.name}"
        resolved.append(replace(item, asset=asset))

    for reason, note in (
        (
            highlights.FREE_DRAW,
            "no geometry to crop; draw a box round it instead",
        ),
        (highlights.NO_TEXT, "selected no text; draw a box round it instead"),
    ):
        pages = [s.page for s in skipped if s.reason == reason]
        if pages:
            where = ", ".join(f"p{page}" for page in pages)
            typer.secho(
                f"skipped {len(pages)} {reason} highlight(s) ({where}): {note}",
                fg=typer.colors.YELLOW,
                err=True,
            )
    return resolved
```

Add `from dataclasses import replace` to the imports.

- [ ] **Step 5: Update the three call sites and the JSON**

`_extract` now takes a keyword argument. In `highlight`, the call becomes
`found = _extract(record, papers_dir=papers_dir)`. Check for other callers with:

```bash
grep -n "_extract(" src/research_assistant/cli.py
```

and pass `papers_dir=` at each. Then `_placed` keys on the asset too:

```python
    return {
        (quote.page, quote.text, quote.asset): heading
        for heading, quotes in groups
        for quote in quotes
    }
```

changing its return annotation to
`dict[tuple[int, str, str | None], str]`, and the JSON output becomes:

```python
                {
                    "order": h.order,
                    "page": h.page,
                    "kind": h.kind,
                    "text": h.text,
                    "asset": h.asset,
                    "group": placed.get((h.page, h.text, h.asset)),
                }
```

Check `_gold_grouping`, `_score_grouping`, `_apply_grouping` and
`_audit_highlights` for other `(page, text)` keys with the same grep and widen
them the same way.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest`
Expected: all pass.

- [ ] **Step 7: Typecheck and lint**

Run: `uv run mypy src/ tests/ && uv run ruff check src/ tests/`
Expected: no errors.

- [ ] **Step 8: Run it against the real vault**

```bash
VAULT_PAPERS_DIR="$HOME/Obsidian/School/Y4S1/CP4101 B.Comp. Dissertation/papers" \
  uv run research-assistant highlights garca2020iotbased
```

Expected: 12 items — 11 quotes and one `"kind": "figure"` on page 7, whose
`asset` is `assets/garca2020iotbased/p07-<digest>.png`, and that file exists
holding the Figure 6 pie chart. Run it a second time and confirm the digest is
unchanged and no new file appears.

- [ ] **Step 9: Commit**

```bash
git add src/research_assistant/cli.py src/research_assistant/search.py \
        src/research_assistant/vault.py tests/test_cli.py
git commit -m "feat(cli): render boxed figures into the vault's assets folder"
```

---

### Task 7: Documentation and the version bump

The note format is the public API and this release adds to it, so the
documentation is not a trailing chore — it is the deliverable the version
number is a promise about.

**Files:**
- Modify: `docs/note-format.md`
- Modify: `README.md`
- Modify: `skills/highlights/SKILL.md`
- Modify: `pyproject.toml` (`version`), `.claude-plugin/plugin.json` (`version`)

**Interfaces:**
- Consumes: everything above. Produces no code.

- [ ] **Step 1: Update `docs/note-format.md`**

Four edits:

1. In the **Layout** block, add under the `pdfs/` entry:
   ```text
   ../assets/
   ├── maioli2021alfred/       cropped figures, one folder per paper
   │   └── p07-3f9a1c2b.png    named for its page and its crop box
   └── …
   ```
   and add `assets/` to the sentence listing what is a sibling of the papers
   folder rather than a child of it.

2. In **What a tool may overwrite**, add a row:
   | `assets/*` | `highlights` | Written once per crop box; never rewritten |

3. Under **Quotes are never altered**, add a short subsection:

   > ### Figures are the box you drew
   >
   > A rectangle drawn round a figure or table — `/Square`, `/Circle`,
   > `/Polygon` or `/Ink`, from any PDF reader — is cropped to
   > `assets/<cite_key>/` and embedded as a bullet in the same `### ` groups
   > the quotes sit in, numbered into the same reading order:
   >
   > ```markdown
   > - ![[assets/garca2020iotbased/p07-3f9a1c2b.png]] ([[garca2020iotbased.pdf#page=7|p. 7]])
   > ```
   >
   > The filename is a digest of the page, the crop box and the render settings,
   > so the same box always resolves to the same file and a re-run rewrites
   > nothing. Nothing chooses a figure for you: no box, no figure.

4. Add a paragraph to the version history beside the 0.3.0 note:

   > 0.5.0 adds the `assets/` folder and the figure embed. Both are additive: a
   > note written by 0.4.0 stays valid, and a vault with no drawn boxes is
   > byte-identical after the upgrade.

- [ ] **Step 2: Update `README.md`**

Add `assets/` to the mermaid diagram — `PDFS -- "highlights" --> ASSETS` and
`ASSETS --> NOTES` — and amend the paragraph beginning "**The only bytes a model
writes into a note are the `### ` headings**" to note that a figure is selected
by the box you drew and cropped verbatim, so it too is chosen by you and never
by a model. Update the install snippet's pin from `@v0.4.0` to `@v0.5.0` and
the surrounding sentence, which currently explains what 0.4.0 changed.

- [ ] **Step 3: Update `skills/highlights/SKILL.md`**

Three edits:

1. The JSON shape becomes
   `{"order": 63, "page": 21, "kind": "quote"|"figure", "text": "…", "asset": "…"|null, "group": "…"|null}`.
2. Add to the "hard constraint" section: a figure is grouped by its order number
   exactly as a quote is, and the model never handles an asset path either.
3. Add a row to the "When it goes wrong" table:
   | `skipped N free-draw` | The annotation records no geometry. Draw a box round the figure instead; any reader's rectangle, circle or polygon works. |

- [ ] **Step 4: Bump both versions**

Set `version = "0.5.0"` in `pyproject.toml` and `"version": "0.5.0"` in
`.claude-plugin/plugin.json`. They must match.

- [ ] **Step 5: Verify the whole check suite**

Run: `just check`
Expected: lint, typecheck and tests all pass.

- [ ] **Step 6: Commit**

```bash
git add docs/note-format.md README.md skills/highlights/SKILL.md \
        pyproject.toml .claude-plugin/plugin.json
git commit -m "docs: specify the figure embed and release 0.5.0"
```

---

## Self-review notes

**Spec coverage.** Selection rules → Task 4. One sequence → Task 4 Step 2's
ordering test. Module boundary → Tasks 1–3 (`figures.py` imports nothing from
the package) and Task 6 (the CLI is the only joiner). Asset location and name →
Tasks 2 and 6. Render settings → Task 3. Encoding → Task 1. Note appearance and
round-trip → Task 5. Audit-for-free → Task 5's `test_a_figure_whose_asset_moved_is_unresolved`.
Tables → no separate task by design; they take the figure path unchanged. Note
format 0.5.0 → Task 7.

**Known soft spot.** Task 3 Step 2 is a probe rather than an assertion, because
`pypdfium2` could not be installed in the sandbox this plan was written in
(`pypi.org` is off the network allowlist). Its bitmap `mode` and `stride` are
the two facts the implementation depends on, so they are established
empirically before the code is written, and Step 7 is a visual check that no
automated test can replace.

**Out of scope, deliberately:** pruning orphaned assets, caption text as a
label, and figures from un-annotated PDFs. All three are argued in the spec.
