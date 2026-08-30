"""Recovering the text under a PDF highlight, and writing it into a note.

Three tiers, each testing what it is good at. The normalisation rules are pure
string functions and need no PDF at all. The geometry needs one, so
``fixtures/make_highlights_pdf.py`` writes it from the standard library rather
than committing a binary nobody can review. The strongest evidence — exact
strings from the real corpus — cannot run in CI, so it is marked ``vault`` and
deselected by default, exactly as ``hil`` already works for hardware.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fixtures.make_highlights_pdf import PAGE_ONE, PAGE_TWO, write

from earth_computers.refs import highlights

if TYPE_CHECKING:
    from collections.abc import Sequence

PDF_NAME = "yen2023soilpowered.pdf"


@pytest.fixture
def fixture_pdf(tmp_path: Path) -> Path:
    """The generated two-page PDF: no space glyphs, several quads per line."""
    return write(tmp_path / "highlights.pdf")


# --- Tier 1: the normalisation rules, on strings ---------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("eﬃcient ﬁlter ﬂow", "efficient filter flow"),
        ("aﬀordable waﬄe", "affordable waffle"),
        ("environ-\nmental impact", "environmental impact"),
        ("self-\npowered sensors", "selfpowered sensors"),
        # A hyphen anywhere but a line end is the author's, and stays.
        ("ultra-low power", "ultra-low power"),
        ("10-20 µW", "10-20 µW"),
        ("battery-free e-ink", "battery-free e-ink"),
        # A break before a capital or a digit is not a broken word.
        ("MSP-\n430 boards", "MSP- 430 boards"),
        ("soil\nmoisture   content", "soil moisture content"),
        ("  leading and trailing  ", "leading and trailing"),
    ],
)
def test_normalise_applies_only_the_stated_rules(raw: str, expected: str) -> None:
    assert highlights.normalise(raw) == expected


def test_normalise_never_repairs_a_source_typo() -> None:
    """Page 4 of the Yen paper really does read ``envrionmental``."""
    assert "envrionmental" in highlights.normalise("low envrionmental impact")


def test_normalise_touches_neither_case_nor_punctuation() -> None:
    """A quote beginning mid-sentence stays lowercase and gains no full stop."""
    assert highlights.normalise("ittle has been done to model") == (
        "ittle has been done to model"
    )
    assert highlights.normalise("about 36◦C according to Zhang et al.)") == (
        "about 36◦C according to Zhang et al.)"
    )


def test_normalise_is_idempotent() -> None:
    once = highlights.normalise("eﬃcient environ-\nmental  ultra-low")
    assert highlights.normalise(once) == once


# --- Tier 2: the geometry, against the generated fixture -------------------


def test_extract_recovers_word_spacing_with_no_space_glyphs(fixture_pdf: Path) -> None:
    """The words are separate ``Tj``s at explicit origins, as LaTeX sets them."""
    found, _ = highlights.extract(fixture_pdf)
    assert found[0].text == PAGE_ONE


def test_extract_stops_at_the_highlight_boundary(fixture_pdf: Path) -> None:
    """``ignored`` shares a line with the selection but is outside every quad."""
    found, _ = highlights.extract(fixture_pdf)
    assert "ignored" not in found[0].text


def test_extract_keeps_two_columns_in_reading_order(fixture_pdf: Path) -> None:
    """The right column sits higher on the page, so position order gets it wrong."""
    found, _ = highlights.extract(fixture_pdf)
    assert found[1].text == PAGE_TWO


def test_extract_numbers_highlights_in_reading_order(fixture_pdf: Path) -> None:
    found, _ = highlights.extract(fixture_pdf)
    assert [(h.order, h.page) for h in found] == [(1, 1), (2, 2)]


def test_extract_skips_a_free_draw_highlight(fixture_pdf: Path) -> None:
    """No ``/QuadPoints`` means no geometry, so there is nothing to recover."""
    found, skipped = highlights.extract(fixture_pdf)
    assert len(found) == 2
    assert [(s.page, s.reason) for s in skipped] == [(2, highlights.FREE_DRAW)]


def test_raising_the_space_threshold_runs_the_words_together(
    fixture_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard on the one tuned constant: 0.20 is above the real word gap."""
    monkeypatch.setattr(highlights, "SPACE_RATIO", 0.20)
    found, _ = highlights.extract(fixture_pdf)
    assert found[0].text == "Solarpanelsareproneto gettingcoveredtoday"


def test_extract_reports_a_malformed_pdf(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4\nnot really\n")
    with pytest.raises(highlights.HighlightError, match=r"broken\.pdf"):
        highlights.extract(broken)


# --- The note region: parsing it back, and writing it ----------------------


def quotes(*pairs: tuple[int, str]) -> list[highlights.Quote]:
    return [highlights.Quote(page=page, text=text) for page, text in pairs]


def render(prose: str, groups: Sequence[tuple[str, list[highlights.Quote]]]) -> str:
    return highlights.render_notes(prose, groups, pdf_name=PDF_NAME)


def test_notes_round_trip_through_render_and_split() -> None:
    groups = [
        ("SMFC power output", quotes((2, "produce as much as 200 µW"))),
        ("Why not solar", quotes((2, "Solar panels are prone to getting covered"))),
    ]
    prose, parsed = highlights.split_notes(render("My own thinking.", groups))
    assert prose == "My own thinking."
    assert parsed == [(name, list(qs)) for name, qs in groups]


def test_a_wrapped_quote_unwraps_to_the_same_string() -> None:
    long = (
        "the v3 cell generated on average 68 times more power than needed for the "
        "MARS tag to operate, and increased its theoretical runtime by 120% compared "
        "to the baseline v0 cell"
    )
    section = render("", [("Power", quotes((21, long)))])
    assert max(len(line) for line in section.splitlines()) <= highlights.WRAP_WIDTH
    _, parsed = highlights.split_notes(section)
    assert parsed[0][1][0].text == long


def test_a_hyphenated_word_is_not_wrapped_across_lines() -> None:
    """Breaking on the hyphen would gain a space when the audit unwraps it."""
    text = " ".join(["padding"] * 11) + " ultra-low-power sensing"
    section = render("", [("Power", quotes((3, text)))])
    _, parsed = highlights.split_notes(section)
    assert parsed[0][1][0].text == text


def test_prose_above_the_first_heading_is_yours() -> None:
    section = (
        f"Some prose.\n\nA second paragraph.\n\n### Group\n\n"
        f"- a quote ([[{PDF_NAME}#page=1|p. 1]])"
    )
    prose, _ = highlights.split_notes(section)
    assert prose == "Some prose.\n\nA second paragraph."


def test_a_section_with_no_heading_is_entirely_prose() -> None:
    """A bullet list of your own is prose too, until a `### ` heading opens."""
    prose, groups = highlights.split_notes("Just my notes.\n\n- my own bullet")
    assert prose == "Just my notes.\n\n- my own bullet"
    assert groups == []


def test_each_quote_is_one_bullet_closed_by_its_page_link() -> None:
    """A tight list: nothing between the bullets."""
    section = render("", [("Power", quotes((21, "a quote"), (2, "another")))])
    assert section.splitlines() == [
        "### Power",
        "",
        f"- a quote ([[{PDF_NAME}#page=21|p. 21]])",
        f"- another ([[{PDF_NAME}#page=2|p. 2]])",
    ]


def test_the_page_link_is_never_split_across_two_lines() -> None:
    """It wraps as one token, so a line never ends on a dangling ``p.``."""
    for padding in range(40, 80):
        section = render("", [("Power", quotes((21, "w " * padding)))])
        lines = section.splitlines()
        assert not any(line.rstrip().endswith("p.") for line in lines)
        assert max(len(line) for line in lines) <= highlights.WRAP_WIDTH


def test_a_quote_ending_in_a_bracket_is_not_mistaken_for_the_link() -> None:
    """Page 5 of the Yen paper really does end a highlight on ``Zhang et al.)``."""
    text = "up to a certain point (about 36◦C according to Zhang et al.)"
    _, parsed = highlights.split_notes(render("", [("Soil", quotes((5, text)))]))
    assert parsed[0][1] == quotes((5, text))


def test_two_identical_quotes_from_different_pages_both_survive() -> None:
    groups = [("Repeated", quotes((2, "the same words"), (9, "the same words")))]
    _, parsed = highlights.split_notes(render("", groups))
    assert parsed[0][1] == groups[0][1]


# --- Grouping by order: the model never handles quote text -----------------


def found_two() -> list[highlights.Highlight]:
    return [
        highlights.Highlight(page=2, text="first quote", order=1),
        highlights.Highlight(page=3, text="second quote", order=2),
    ]


def test_group_quotes_resolves_orders_to_the_extractor_s_own_text() -> None:
    groups = highlights.group_quotes(found_two(), {"A": [2], "B": [1]})
    assert groups == [
        ("A", quotes((3, "second quote"))),
        ("B", quotes((2, "first quote"))),
    ]


@pytest.mark.parametrize(
    ("grouping", "message"),
    [
        ({"A": [1]}, "drops 1"),
        ({"A": [1, 2], "B": [2]}, "repeats 1"),
        ({"A": [1, 2, 9]}, "no highlight for"),
    ],
)
def test_group_quotes_refuses_to_drop_repeat_or_invent(
    grouping: dict[str, list[int]], message: str
) -> None:
    with pytest.raises(highlights.HighlightError, match=message):
        highlights.group_quotes(found_two(), grouping)


# --- Tier 3: the real corpus ------------------------------------------------


def yen_pdf() -> Path:
    papers = Path(
        os.environ.get(
            "VAULT_PAPERS_DIR",
            "~/Obsidian/School/Y4S1/CP4101 B.Comp. Dissertation/papers",
        )
    ).expanduser()
    return papers.parent / "pdfs" / PDF_NAME


@pytest.fixture
def yen() -> list[highlights.Highlight]:
    path = yen_pdf()
    if not path.is_file():
        pytest.skip(f"{path} is not on this machine")
    found, _ = highlights.extract(path)
    return found


@pytest.mark.vault
def test_the_yen_paper_has_eighty_extractable_highlights() -> None:
    path = yen_pdf()
    if not path.is_file():
        pytest.skip(f"{path} is not on this machine")
    found, skipped = highlights.extract(path)
    assert len(found) == 80
    assert [(s.page, s.reason) for s in skipped] == [
        (7, highlights.FREE_DRAW),
        (15, highlights.FREE_DRAW),
    ]


@pytest.mark.vault
def test_the_twenty_six_quad_highlight_reads_in_order(
    yen: list[highlights.Highlight],
) -> None:
    """The one that per-quad-then-sort-by-top corrupted."""
    quote = next(h for h in yen if h.page == 22 and "boost" in h.text)
    assert quote.text == (
        "the new v3 cell design is more suitable for computing due to its ability to "
        "achieve an over 40% boost in the number of operations each digital system "
        "(i.e., Advanced and Minimal) can execute while giving the Analog system a "
        "120% increase in runtime"
    )


@pytest.mark.vault
def test_the_page_five_quote_is_spaced(yen: list[highlights.Highlight]) -> None:
    """The one a 0.20 space threshold ran together."""
    assert any(h.text == "SMFCs use a layer of soil as the electrolyte" for h in yen)


@pytest.mark.vault
def test_a_source_typo_survives_into_the_quote(yen: list[highlights.Highlight]) -> None:
    quote = next(h for h in yen if "Carbon felt is a popular choice" in h.text)
    assert quote.page == 4
    assert "low envrionmental impact" in quote.text


@pytest.mark.vault
def test_a_mid_word_selection_is_preserved_verbatim(
    yen: list[highlights.Highlight],
) -> None:
    """A bad selection is faithful output, not a bug. Re-highlight to fix it."""
    assert any(h.text.startswith("ittle has been done to model") for h in yen)


@pytest.mark.vault
def test_every_quote_is_derivable_from_the_rules_alone(
    yen: list[highlights.Highlight],
) -> None:
    """The fidelity property: normalisation is the only transformation there is."""
    for quote in yen:
        assert highlights.normalise(quote.text) == quote.text


@pytest.mark.vault
def test_the_whole_corpus_round_trips_through_a_note(
    yen: list[highlights.Highlight],
) -> None:
    grouping = {"Everything": [h.order for h in yen]}
    groups = highlights.group_quotes(yen, grouping)
    section = highlights.render_notes("Prose.", groups, pdf_name=PDF_NAME)
    prose, parsed = highlights.split_notes(section)
    assert prose == "Prose."
    assert [(q.page, q.text) for q in parsed[0][1]] == [(h.page, h.text) for h in yen]


@pytest.mark.vault
def test_a_grouping_file_is_ordinary_json(yen: list[highlights.Highlight]) -> None:
    """What the skill writes: headings to orders, never a byte of quote text."""
    grouping = json.loads(json.dumps({"Everything": [h.order for h in yen]}))
    assert highlights.group_quotes(yen, grouping)
