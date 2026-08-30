"""Write the fixture PDF the geometry tests run against, using only the stdlib.

Hand-highlighting a real paper in Obsidian and committing the binary would put
the inputs to these tests beyond review. This emits the PDF instead, so the
words, their positions and every quad are visible in the diff.

Two conditions from the real corpus are reproduced deliberately:

* **No space glyphs.** Each word is its own ``Tj`` at an explicit ``Tm`` origin,
  exactly as LaTeX sets a justified line. Word spacing must therefore be
  recovered from the gaps, which is what broke ``extract_words()``.
* **Several quads per line, with unequal tops.** Sorting quad fragments by their
  top then scrambles the word order within a line — the failure that produced
  ``an over boost in the number of operations each system``.

The font declares a uniform 500/1000 width, so every glyph advances exactly half
the point size and each word's extent is arithmetic rather than a guess about a
real face's metrics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from pathlib import Path

SIZE = 10.0
ADVANCE = SIZE / 2  # the /Widths entry below, scaled
# 0.15 of the point size: above the 0.1 threshold, below the 0.20 an earlier
# draft used, so raising the threshold again runs the words together here.
GAP = 1.5
ASCENT, DESCENT = 9.0, 3.0

# What Obsidian writes for a free-draw highlight: no /QuadPoints, and a /Rect
# spanning the whole float range.
FLT_MAX = "340282346638528859811704183484516925440"

PAGE_ONE = "Solar panels are prone to getting covered today"
PAGE_TWO = "end of left start of right"


class Word(NamedTuple):
    text: str
    x: float
    baseline: float


def _line(words: str, x: float, baseline: float) -> list[Word]:
    """Lay a line out left to right, leaving a real gap between words."""
    placed: list[Word] = []
    for word in words.split():
        placed.append(Word(word, x, baseline))
        x += ADVANCE * len(word) + GAP
    return placed


def _quad(word: Word, index: int) -> list[float]:
    """The quad covering one word, as ``x1 y1 x2 y2 x3 y3 x4 y4``.

    Alternate words sit 1.5pt taller. The glyphs do not move — only the quad —
    which is what makes sorting fragments by their top scramble a line.
    """
    top = word.baseline + ASCENT + (1.5 if index % 2 else 0.0)
    bottom = word.baseline - DESCENT
    left, right = word.x - 0.5, word.x + ADVANCE * len(word.text) + 0.5
    return [left, top, right, top, left, bottom, right, bottom]


def _stream(words: list[Word]) -> bytes:
    lines = ["BT", f"/F1 {SIZE:g} Tf"]
    lines += [f"1 0 0 1 {w.x:g} {w.baseline:g} Tm ({w.text}) Tj" for w in words]
    lines.append("ET")
    return "\n".join(lines).encode("ascii")


def _highlight(words: list[Word]) -> str:
    quads = [n for index, word in enumerate(words) for n in _quad(word, index)]
    xs, ys = quads[0::2], quads[1::2]
    rect = f"[{min(xs):g} {min(ys):g} {max(xs):g} {max(ys):g}]"
    numbers = " ".join(f"{n:g}" for n in quads)
    return (
        f"<</Type/Annot/Subtype/Highlight/Rect {rect}"
        f"/QuadPoints [{numbers}]/C [1 1 0]>>"
    )


def build() -> bytes:
    """The whole PDF: two pages, two selection highlights, one free-draw."""
    # Page 1 — one column, one highlight spanning two lines. `ignored` sits on
    # the second line but outside every quad, so it proves the char selection
    # stops at the highlight's boundary rather than taking the whole line.
    first = _line("Solar panels are prone to", 80, 700)
    second = _line("getting covered today ignored", 80, 680)
    page_one_words = first + second
    page_one_quads = first + second[:3]

    # Page 2 — two columns, one highlight running from the foot of the left
    # column into the head of the right. Its quads are in reading order, but the
    # right column's are 20pt *higher* on the page, so anything that sorts by
    # position emits the columns the wrong way round.
    left = _line("body text here", 53, 700) + _line("end of left", 53, 680)
    right = _line("start of right", 317, 700) + _line("more body text", 317, 680)
    page_two_words = left + right
    page_two_quads = left[3:] + right[:3]

    free_draw = (
        f"<</Type/Annot/Subtype/Highlight/Rect [0 0 {FLT_MAX} {FLT_MAX}]/AP null>>"
    )

    streams = [_stream(page_one_words), _stream(page_two_words)]
    objects: list[bytes] = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids [3 0 R 5 0 R]/Count 2>>",
        (
            "<</Type/Page/Parent 2 0 R/MediaBox [0 0 612 792]"
            "/Resources <</Font <</F1 7 0 R>>>>/Contents 4 0 R"
            f"/Annots [{_highlight(page_one_quads)}]>>"
        ).encode("ascii"),
        b"",  # 4: page 1 content stream, filled in below
        (
            "<</Type/Page/Parent 2 0 R/MediaBox [0 0 612 792]"
            "/Resources <</Font <</F1 7 0 R>>>>/Contents 6 0 R"
            f"/Annots [{free_draw} {_highlight(page_two_quads)}]>>"
        ).encode("ascii"),
        b"",  # 6: page 2 content stream
        (
            # Deliberately not one of the standard 14 names: pdfminer prefers
            # its own metrics for those and ignores the /Widths below.
            "<</Type/Font/Subtype/Type1/BaseFont/Uniform500"
            "/FirstChar 32/LastChar 126/FontDescriptor 8 0 R"
            "/Widths [" + " ".join(["500"] * 95) + "]>>"
        ).encode("ascii"),
        (
            # Only for the /FontBBox: without one pdfminer warns on every page.
            "<</Type/FontDescriptor/FontName/Uniform500/Flags 32"
            "/FontBBox [0 -200 500 800]/ItalicAngle 0/Ascent 800/Descent -200"
            "/CapHeight 700/StemV 80>>"
        ).encode("ascii"),
    ]
    for index, stream in zip((3, 5), streams, strict=True):
        objects[index] = (
            f"<</Length {len(stream)}>>\nstream\n".encode("ascii")
            + stream
            + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"

    start = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<</Size {len(objects) + 1}/Root 1 0 R>>\nstartxref\n{start}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


def write(path: Path) -> Path:
    """Write the fixture PDF to ``path`` and return it."""
    path.write_bytes(build())
    return path
