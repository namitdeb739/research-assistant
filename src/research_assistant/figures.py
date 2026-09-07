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

import hashlib
import struct
import zlib
from typing import TYPE_CHECKING

import pypdfium2 as pdfium  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from pathlib import Path

# PNG colour type 2 is truecolour: three 8-bit channels, no palette, no alpha.
BIT_DEPTH = 8
COLOUR_TYPE = 2
CHANNELS = 3
SIGNATURE = b"\x89PNG\r\n\x1a\n"

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
    header = struct.pack(">IIBBBBB", width, height, BIT_DEPTH, COLOUR_TYPE, 0, 0, 0)
    return (
        SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


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
