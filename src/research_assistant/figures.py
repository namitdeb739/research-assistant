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
    header = struct.pack(">IIBBBBB", width, height, BIT_DEPTH, COLOUR_TYPE, 0, 0, 0)
    return (
        SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )
