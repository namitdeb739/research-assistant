"""Recover the text under Obsidian's PDF highlights, verbatim.

A highlight made in Obsidian's built-in viewer is stored as a ``/Highlight``
annotation with ``/QuadPoints`` and no ``/Contents``: the geometry is saved, the
text is not. Recovering it means intersecting those quads with the page's text
layer, character by character.

Nothing here alters a quote. The only transformations are the mechanical
normalisation rules in :func:`normalise`, applied identically when a quote is
written into a note and when the audit re-derives it. The failure this guards
against is small and specific: page 4 of the Yen paper reads ``low envrionmental
impact``, a typo in the published source, and any pass that tidies a quote makes
the note misquote the paper. So there is no model in this module, and no rule
that touches case or punctuation.
"""

from __future__ import annotations

import math
import re
import textwrap
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pdfminer.converter import PDFPageAggregator
from pdfminer.layout import LTChar, LTContainer
from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdftypes import resolve1

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from pathlib import Path

# A gap wider than this fraction of the character's size is a word break. The
# distribution is sharply bimodal. Measured on page 5 of the Yen paper,
# intra-word gaps are 0.00pt (slightly negative under kerning) and inter-word
# gaps are 1.96pt at size 9.96, a ratio of 0.197, so this sits in the middle of
# a wide empty band. An earlier 0.20 was just above the real gap and silently
# produced `SMFCsusealayerofsoilastheelectrolyte`.
SPACE_RATIO = 0.1

# LaTeX sets these as single glyphs; nothing downstream wants them.
LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}

# One quote is one bullet, closed by a bracketed link to its page. The text is
# hard-wrapped to match the repo's markdown, with continuation lines indented to
# sit inside the list item; the audit unwraps by joining them with one space,
# which is lossless because normalisation has already collapsed every internal
# run of whitespace.
WRAP_WIDTH = 88
BULLET = "- "
CONTINUATION = "  "
HEADING_PREFIX = "### "

# An Obsidian embed, as one unbreakable token: an asset path holds no spaces,
# so unlike a page link it needs no placeholder while wrapping.
EMBED_OPEN = "![["
EMBED_CLOSE = "]]"

FREE_DRAW = "free-draw"
NO_TEXT = "no text"

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

# A heading with one quote under it is fine; a heading per quote is not a
# grouping. Fewer headings than this and there is nothing to group, so the check
# would fire only on notes holding two or three highlights in total.
SINGLETON_HEADING_FLOOR = 3


class HighlightError(Exception):
    """Raised when a PDF cannot be read, or a note cannot be written."""


@dataclass(frozen=True, slots=True)
class Highlight:
    """One highlighted passage or one boxed figure, as the PDF has it."""

    page: int
    text: str
    order: int
    kind: str = KIND_QUOTE
    rect: tuple[float, float, float, float] | None = None
    asset: str | None = None


@dataclass(frozen=True, slots=True)
class Skipped:
    """A highlight with no recoverable text, and why."""

    page: int
    reason: str


@dataclass(frozen=True, slots=True)
class Quote:
    """One bullet as it appears in a note: the text or embed, and its page."""

    page: int
    text: str
    asset: str | None = None


@dataclass(frozen=True, slots=True)
class Scores:
    """How close one grouping of the same quotes is to another."""

    adjusted_rand: float
    homogeneity: float
    completeness: float
    v_measure: float


def _name(value: Any) -> str | None:
    """A PDF name object's value; ``/Highlight`` arrives as ``PSLiteral``."""
    resolved = resolve1(value)
    name = getattr(resolved, "name", resolved)
    return name if isinstance(name, str) else None


def _quads(annot: Mapping[str, Any]) -> list[tuple[float, ...]]:
    """``/QuadPoints`` as 8-number groups, *in array order*.

    Array order is what pdf.js writes, and pdf.js writes selection order, which
    is reading order. Ordering by anything derived from the geometry instead
    (clustering by y, sorting fragments by top) needs a notion of "line" that
    two-column papers do not have, and interleaves the columns.
    """
    points = resolve1(annot.get("QuadPoints"))
    if not isinstance(points, list) or len(points) < 8:
        return []
    numbers = [float(p) for p in points]
    return [tuple(numbers[i : i + 8]) for i in range(0, len(numbers) - 7, 8)]


def _rect(
    annot: Mapping[str, Any], box: Sequence[float]
) -> tuple[float, float, float, float] | None:
    """A drawn shape's ``/Rect``, normalised and clipped to the page.

    ``None`` when the rectangle is unusable: inverted to nothing by the clip,
    a stray click, or the whole-page rectangle a free-draw writes.
    """
    values = resolve1(annot.get("Rect"))
    if not isinstance(values, list) or len(values) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in values)
    except TypeError, ValueError:
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


def _as_quad(rect: tuple[float, ...]) -> tuple[float, ...]:
    """A rectangle in the 8-number quad form ``_order_key`` reads."""
    x0, y0, x1, y1 = rect
    return (x0, y1, x1, y1, x0, y0, x1, y0)


def _chars(container: LTContainer[Any]) -> Iterator[LTChar]:
    for item in container:
        if isinstance(item, LTChar):
            yield item
        elif isinstance(item, LTContainer):
            yield from _chars(item)


def _inside(
    char: LTChar, quad: tuple[float, ...], to_page_x: float, to_page_y: float
) -> bool:
    """Whether the character's *centre* falls in the quad.

    The centre, not any overlap: a quad's edge cuts through the glyphs either
    side of the selection boundary, and admitting those over-captures.

    ``to_page_x`` and ``to_page_y`` carry the character out of pdfminer's
    origin-normalised space and into the page's own, which is where the quad
    already lives. They are named for the space they arrive in rather than for
    a delta, because the direction is the whole of what this has to get right.
    """
    xs, ys = quad[0::2], quad[1::2]
    cx = (char.x0 + char.x1) / 2 + to_page_x
    cy = (char.y0 + char.y1) / 2 + to_page_y
    return min(xs) <= cx <= max(xs) and min(ys) <= cy <= max(ys)


def _join(chars: Sequence[LTChar]) -> str:
    """Concatenate a run of characters, restoring the whitespace the PDF omits.

    A wrap is emitted as a newline rather than a space so that de-hyphenation in
    :func:`normalise` can tell a broken word from a real hyphen; rule 5 collapses
    it afterwards.
    """
    parts: list[str] = []
    previous: LTChar | None = None
    for char in chars:
        if previous is not None:
            if char.x0 < previous.x0:
                parts.append("\n")
            elif char.x0 - previous.x1 > SPACE_RATIO * previous.size:
                parts.append(" ")
        parts.append(char.get_text())
        previous = char
    return "".join(parts)


def normalise(text: str) -> str:
    """Apply rules 2 to 5 to raw extracted text. Case and punctuation are untouched.

    Everything the page has is kept: source typos, the degree-ring in ``36◦C``,
    en-dashes, lowercase openings on quotes that begin mid-sentence, and
    unbalanced parens where a highlight stopped short of a ``)``.
    """
    for ligature, expansion in LIGATURES.items():
        text = text.replace(ligature, expansion)
    text = unicodedata.normalize("NFC", text)
    # A hyphen that ends a line before a lowercase letter is a broken word. One
    # anywhere else is the author's: `ultra-low`, `e-ink` and `10-20 µW` survive.
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "-" and (rest := text[index + 1 :]).startswith("\n"):
            tail = rest.lstrip("\n")
            if tail[:1].islower():
                index += len(rest) - len(tail) + 1
                continue
        out.append(char)
        index += 1
    return " ".join("".join(out).split())


def _order_key(page: int, quad: tuple[float, ...]) -> tuple[int, float, float]:
    """Document reading order: page, then the first quad's top, then its left.

    Not ``/Annots`` array order, which is creation order and interleaves badly:
    on page 2 of the Yen paper the first-created highlight sits below the third.
    """
    return (page, -max(quad[1::2]), min(quad[0::2]))


def extract(path: Path) -> tuple[list[Highlight], list[Skipped]]:
    """Every recoverable highlight in a PDF, in reading order, plus what was not.

    ``order`` is the passage's 1-based position in reading order, and is the
    identity the note writer addresses a quote by, so the model never handles
    quote text at all.
    """
    found: list[
        tuple[
            tuple[int, float, float],
            int,
            str,
            str,
            tuple[float, float, float, float] | None,
        ]
    ] = []
    skipped: list[Skipped] = []
    manager = PDFResourceManager()
    device = PDFPageAggregator(manager, laparams=None)
    interpreter = PDFPageInterpreter(manager, device)
    try:
        with path.open("rb") as handle:
            for number, page in enumerate(PDFPage.get_pages(handle), start=1):
                annots = resolve1(page.annots)
                if not isinstance(annots, list):
                    continue
                interpreter.process_page(page)
                chars = list(_chars(device.get_result()))
                # pdfminer translates the mediabox origin to (0, 0); the quads
                # are still in the page's own user space. So the origin is
                # added *back* onto a character to compare the two. Subtracting
                # it here instead moved the glyph twice the offset away from
                # its quad, matching nothing, and the passage was lost rather
                # than corrupted. The names say which space they land in,
                # because a sign is the one thing this cannot get wrong twice.
                to_page_x = float(page.mediabox[0])
                to_page_y = float(page.mediabox[1])
                for annot in annots:
                    resolved = resolve1(annot)
                    if not isinstance(resolved, dict):
                        continue
                    subtype = _name(resolved.get("Subtype"))
                    if subtype in FIGURE_SUBTYPES:
                        rect = _rect(resolved, page.mediabox)
                        if rect is None:
                            skipped.append(Skipped(number, FREE_DRAW))
                            continue
                        # `rect` is already shifted to the mediabox origin, for
                        # the renderer (`_rect`'s docstring). A quote's order
                        # key is built from its raw, untranslated quad, so
                        # ordering `rect` directly would compare the two arms
                        # in different coordinate spaces: on a page whose
                        # mediabox does not start at (0, 0), a figure would
                        # sort as if it sat lower than it does. Adding the
                        # origin back reverses `_rect`'s `-px0, -py0`, so the
                        # key lands in the same page-user-space the quote arm
                        # uses, and the shifted `rect` is kept for the renderer.
                        order_quad = _as_quad(
                            (
                                rect[0] + to_page_x,
                                rect[1] + to_page_y,
                                rect[2] + to_page_x,
                                rect[3] + to_page_y,
                            )
                        )
                        found.append(
                            (
                                _order_key(number, order_quad),
                                number,
                                "",
                                KIND_FIGURE,
                                rect,
                            )
                        )
                        continue
                    if subtype != "Highlight":
                        continue
                    quads = _quads(resolved)
                    if not quads:
                        skipped.append(Skipped(number, FREE_DRAW))
                        continue
                    selected: list[LTChar] = []
                    seen: set[int] = set()
                    for quad in quads:
                        run = sorted(
                            (
                                c
                                for c in chars
                                if _inside(c, quad, to_page_x, to_page_y)
                            ),
                            key=lambda c: c.x0,
                        )
                        # Quads overlap, so a character must not be emitted twice.
                        for char in run:
                            if id(char) not in seen:
                                seen.add(id(char))
                                selected.append(char)
                    text = normalise(_join(selected))
                    if not text:
                        skipped.append(Skipped(number, NO_TEXT))
                        continue
                    found.append(
                        (_order_key(number, quads[0]), number, text, KIND_QUOTE, None)
                    )
    except HighlightError:
        raise
    except Exception as exc:
        raise HighlightError(f"{path}: {exc}") from exc

    found.sort(key=lambda entry: entry[0])
    highlights = [
        Highlight(page=page, text=text, order=index, kind=kind, rect=rect)
        for index, (_, page, text, kind, rect) in enumerate(found, start=1)
    ]
    return highlights, skipped


def page_link(pdf_name: str, page: int) -> str:
    """The Obsidian link a quote carries back to its page in the PDF."""
    return f"[[{pdf_name}#page={page}|p. {page}]]"


# Stands in for the spaces inside a page link while the item is wrapped. It
# cannot occur in extracted text: normalisation collapses whitespace and keeps
# only what the page's glyphs produced.
_GUARD = "\x00"

# The link closing a bullet. Anchored at the end, so a quote that itself ends in
# a bracket, `(about 36◦C according to Zhang et al.)`, is not mistaken for one.
_TRAILING_LINK = re.compile(r"\s*\(\[\[[^\[\]]*#page=(\d+)[^\[\]]*\]\]\)$")


def split_notes(section: str) -> tuple[str, list[tuple[str, list[Quote]]]]:
    """Split a ``## Notes`` section into your prose and the machine-owned region.

    The section has no delimiters, so the contract is positional: everything
    before the first ``### `` heading is yours and is reattached unchanged, and
    everything from that heading on belongs to this module. A note with no
    ``### `` heading is therefore entirely yours.
    """
    lines = section.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith(HEADING_PREFIX)),
        len(lines),
    )
    prose = "\n".join(lines[:start]).strip()

    groups: list[tuple[str, list[Quote]]] = []
    block: list[str] = []

    def flush() -> None:
        """Close the item under construction, unwrapping it back to one line.

        Joining the wrapped lines with a single space is lossless: normalisation
        already collapsed every run of whitespace inside a quote.
        """
        if not block or not groups:
            block.clear()
            return
        joined = " ".join(block).strip()
        match = _TRAILING_LINK.search(joined)
        text = joined[: match.start()].rstrip() if match else ""
        if text and match is not None:
            page = int(match.group(1))
            if text.startswith(EMBED_OPEN) and text.endswith(EMBED_CLOSE):
                asset = text[len(EMBED_OPEN) : -len(EMBED_CLOSE)]
                groups[-1][1].append(Quote(page=page, text="", asset=asset))
            else:
                groups[-1][1].append(Quote(page=page, text=text))
        block.clear()

    for line in lines[start:]:
        if line.startswith(HEADING_PREFIX):
            flush()
            groups.append((line[len(HEADING_PREFIX) :].strip(), []))
        elif line.startswith(BULLET):
            # A bullet always opens an item, so items need no blank line
            # between them to be told apart.
            flush()
            block.append(line[len(BULLET) :].strip())
        elif line.startswith(CONTINUATION) and line.strip():
            block.append(line.strip())
        else:
            flush()
    flush()
    return prose, groups


def _item(quote: Quote, *, pdf_name: str) -> str:
    """One bullet: the quote or the embed, then its page link, wrapped to width.

    The link's own spaces are held by a placeholder while wrapping, so it is one
    unbreakable token and never ends up split across two lines. An asset path
    needs no such guard: it holds no spaces to begin with.
    """
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


def render_notes(
    prose: str, groups: Sequence[tuple[str, Sequence[Quote]]], *, pdf_name: str
) -> str:
    """Render a whole ``## Notes`` section: prose first, then the quote groups."""
    blocks = [prose] if prose else []
    for heading, quotes in groups:
        if not quotes:
            continue
        items = [_item(quote, pdf_name=pdf_name) for quote in quotes]
        # A tight list: one bullet per quote, nothing between them.
        blocks.append(f"{HEADING_PREFIX}{heading}\n\n" + "\n".join(items))
    return "\n\n".join(blocks)


def group_quotes(
    highlights: Sequence[Highlight],
    grouping: Mapping[str, Sequence[int]],
    *,
    placed: Mapping[int, str] | None = None,
) -> list[tuple[str, list[Quote]]]:
    """Turn ``{heading: [order, ...]}`` into groups of quotes.

    Orders, not text: the caller cannot alter a quote because it never holds
    one. Every highlight must appear exactly once, so a grouping cannot drop,
    duplicate or invent a quote either.

    ``placed`` maps an order to the heading that quote already sits under in the
    note, and a grouping that moves one somewhere else is refused. Without it a
    re-run is free to reshuffle quotes nobody touched, which is the whole of the
    churn: with it, the only thing a second pass can change is where the *new*
    quotes land. Regrouping a note wholesale is a deliberate act, so it is the
    caller that omits ``placed`` to allow it.
    """
    by_order = {highlight.order: highlight for highlight in highlights}
    seen: list[int] = [order for orders in grouping.values() for order in orders]
    unknown = sorted({order for order in seen if order not in by_order})
    if unknown:
        raise HighlightError(
            f"grouping names {len(unknown)} order(s) this PDF has no highlight for: "
            f"{', '.join(str(o) for o in unknown[:8])}"
        )
    duplicated = sorted({order for order in seen if seen.count(order) > 1})
    if duplicated:
        raise HighlightError(
            f"grouping repeats {len(duplicated)} order(s): "
            f"{', '.join(str(o) for o in duplicated[:8])}"
        )
    missing = sorted(set(by_order) - set(seen))
    if missing:
        raise HighlightError(
            f"grouping drops {len(missing)} of {len(by_order)} highlight(s): "
            f"{', '.join(str(o) for o in missing[:8])}"
        )

    heading_of = {
        order: heading for heading, orders in grouping.items() for order in orders
    }
    moved = sorted(
        (order, was, heading_of[order])
        for order, was in (placed or {}).items()
        if order in heading_of and heading_of[order] != was
    )
    if moved:
        shown = "; ".join(
            f"{order} {was!r} -> {now!r}" for order, was, now in moved[:4]
        )
        raise HighlightError(
            f"grouping moves {len(moved)} already-grouped quote(s) to another "
            f"heading, and headings are spelled exactly as the note has them: "
            f"{shown}. Pass --regroup to rewrite the whole grouping."
        )

    singletons = sum(1 for orders in grouping.values() if len(orders) == 1)
    if len(grouping) >= SINGLETON_HEADING_FLOOR and singletons * 2 > len(grouping):
        raise HighlightError(
            f"grouping is a heading per quote: {singletons} of {len(grouping)} "
            f"headings hold a single quote. Name the idea quotes share."
        )

    return [
        (
            heading,
            [
                Quote(
                    page=by_order[order].page,
                    text=by_order[order].text,
                    asset=by_order[order].asset,
                )
                for order in orders
            ],
        )
        for heading, orders in grouping.items()
    ]


def grouping_of(
    highlights: Sequence[Highlight], groups: Sequence[tuple[str, Sequence[Quote]]]
) -> tuple[dict[str, list[int]], list[Quote]]:
    """The ``{heading: [order, ...]}`` behind a note, and what it could not place.

    The inverse of :func:`group_quotes`. A note that has already been written is
    itself a grouping, one you approved, so recovering it costs nothing and
    turns every paper you have read into a case to measure a grouper against. A
    quote the PDF no longer produces is drift, and is handed back rather than
    guessed at; ``--audit`` is what explains it.
    """
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


def _pairs(count: int) -> float:
    """How many pairs a group of ``count`` quotes contributes."""
    return count * (count - 1) / 2


def score_groupings(
    gold: Mapping[str, Sequence[int]], candidate: Mapping[str, Sequence[int]]
) -> Scores:
    """Compare two groupings of the same quotes, by the pairs they agree on.

    Both measures read only the partition, never a heading's wording, so a
    rename costs nothing and what is scored is the one thing a grouper is being
    asked to get right. The adjusted Rand index is chance-corrected: a grouping
    no better than random scores about zero, and one that splits or merges is
    penalised either way. V-measure adds the direction the ARI hides:
    homogeneity falls when a heading mixes two ideas, completeness when one idea
    is spread over two headings, and those are different mistakes.
    """
    gold_of = {order: heading for heading, orders in gold.items() for order in orders}
    candidate_of = {
        order: heading for heading, orders in candidate.items() for order in orders
    }
    if set(gold_of) != set(candidate_of):
        raise HighlightError(
            "the two groupings do not cover the same quotes: "
            f"{len(set(gold_of) ^ set(candidate_of))} order(s) appear in one only"
        )
    total = len(gold_of)
    if total == 0:
        raise HighlightError("nothing to score: the note holds no grouped quotes")

    table = Counter((gold_of[order], candidate_of[order]) for order in gold_of)
    rows = Counter(gold_of.values())
    columns = Counter(candidate_of.values())

    agreed = sum(_pairs(count) for count in table.values())
    in_gold = sum(_pairs(count) for count in rows.values())
    in_candidate = sum(_pairs(count) for count in columns.values())
    expected = in_gold * in_candidate / _pairs(total) if total > 1 else 0.0
    largest = (in_gold + in_candidate) / 2
    # Both partitions trivial and identical: no pair carries information, and
    # the two agree on all of it.
    rand = 1.0 if largest == expected else (agreed - expected) / (largest - expected)

    shared = sum(
        (count / total) * math.log(count * total / (rows[g] * columns[c]))
        for (g, c), count in table.items()
    )
    spread_gold = -sum((n / total) * math.log(n / total) for n in rows.values())
    spread_candidate = -sum((n / total) * math.log(n / total) for n in columns.values())
    # A single heading holds no information to lose, so it is trivially pure.
    homogeneity = 1.0 if spread_gold == 0 else shared / spread_gold
    completeness = 1.0 if spread_candidate == 0 else shared / spread_candidate
    combined = homogeneity + completeness
    return Scores(
        adjusted_rand=rand,
        homogeneity=homogeneity,
        completeness=completeness,
        v_measure=0.0 if combined == 0 else 2 * homogeneity * completeness / combined,
    )
