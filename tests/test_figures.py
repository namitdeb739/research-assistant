"""Cropping a region of a PDF to a deterministic asset on disk.

The encoder is a pure byte function and is tested by decoding what it wrote
back with ``zlib``: a PNG that a reader cannot invert is not a PNG.
"""

from __future__ import annotations

import struct
import zlib
from typing import TYPE_CHECKING

import pytest
from fixtures.make_highlights_pdf import write

from research_assistant import figures

if TYPE_CHECKING:
    from pathlib import Path

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
    path = figures.render(pdf, 1, (100.0, 600.0, 400.0, 750.0), into=tmp_path / "a")
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
