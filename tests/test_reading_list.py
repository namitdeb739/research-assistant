"""``Reading List.md``: the pending ledger rows, rendered.

A pure function of local files, so nothing here needs a client or a vault. What
is pinned is that each candidate appears exactly once, that a table survives a
title with a pipe in it, and that an empty list still renders.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from research_assistant import reading_list, screening

if TYPE_CHECKING:
    from pathlib import Path


def row(**kwargs: object) -> screening.Decision:
    fields: dict[str, object] = {
        "decided": "2026-09-04T11:20:03+00:00",
        "decision": screening.PENDING,
        "title": "Untitled",
    }
    fields.update(kwargs)
    return screening.Decision(**fields)  # type: ignore[arg-type]


def test_a_multi_seed_candidate_leads_and_is_never_repeated_below() -> None:
    """Each candidate appears exactly once, in the strongest section it earns."""
    rows = [
        row(openalex_id="W1", title="Both", seeds=("W8", "W9")),
        row(openalex_id="W2", title="Only eight", seeds=("W8",)),
    ]

    note = reading_list.render(rows, notes={"W8": "Mementos"}, updated="2026-09-04")

    assert "## Reached from several of your papers (1)" in note
    assert "## From [[Mementos]] (1)" in note
    assert note.count("`W1`") == 1


def test_a_candidate_with_no_seeds_is_unattributed() -> None:
    note = reading_list.render(
        [row(openalex_id="W1", title="Adrift")], notes={}, updated="2026-09-04"
    )

    assert "## Unattributed (1)" in note


def test_a_seed_whose_note_was_deleted_renders_as_its_bare_id() -> None:
    """Not an error: `expand --report` is where that disagreement is surfaced."""
    note = reading_list.render(
        [row(openalex_id="W1", seeds=("W300",))], notes={}, updated="2026-09-04"
    )

    assert "## From W300 (no note in the vault) (1)" in note


def test_a_pipe_in_a_title_is_escaped_so_the_table_survives() -> None:
    note = reading_list.render(
        [row(openalex_id="W1", title="Push | Pull", venue="A|B")],
        notes={},
        updated="2026-09-04",
    )

    assert r"Push \| Pull" in note
    assert r"A\|B" in note


def test_an_empty_list_still_renders_and_says_so() -> None:
    """A stale note left behind by an earlier run would otherwise lie."""
    note = reading_list.render([], notes={}, updated="2026-09-04")

    assert "pending: 0" in note
    assert "Nothing is pending" in note


def test_a_v1_row_renders_with_the_triage_columns_blank() -> None:
    note = reading_list.render(
        [row(openalex_id="W1", title="Old", year=2011)],
        notes={},
        updated="2026-09-04",
    )

    assert "| 2011 | [Old](https://openalex.org/W1) |  |  |  |  | `W1` |" in note


def test_the_title_links_to_the_doi_when_there_is_one() -> None:
    note = reading_list.render(
        [row(openalex_id="W1", doi="10.1145/1950365", title="Mementos")],
        notes={},
        updated="2026-09-04",
    )

    assert "[Mementos](https://doi.org/10.1145/1950365)" in note


def test_rows_rank_by_seeds_then_citations_then_year() -> None:
    ordered = reading_list.order(
        [
            row(openalex_id="W1", title="one seed, famous", citations=900, year=2001),
            row(openalex_id="W2", title="two seeds", seeds=("A", "B"), year=1999),
            row(openalex_id="W3", title="one seed, obscure", citations=2, year=2024),
        ]
    )

    assert [r.openalex_id for r in ordered] == ["W2", "W1", "W3"]


def test_the_selector_intersects_seeds_and_routes() -> None:
    rows = [
        row(openalex_id="W1", seeds=("A",), via=("citation",)),
        row(openalex_id="W2", seeds=("B",), via=("citation",)),
        row(openalex_id="W3", seeds=("A",), via=("reference",)),
    ]

    selected = reading_list.select(
        rows, reading_list.Selector(seeds=("A",), via=("citation",))
    )

    assert [r.openalex_id for r in selected] == ["W1"]


def test_a_floor_drops_a_candidate_whose_value_is_unknown() -> None:
    """The same stance `_select` takes on a work OpenAlex gives no year for."""
    rows = [row(openalex_id="W1", year=None), row(openalex_id="W2", year=2020)]

    selected = reading_list.select(rows, reading_list.Selector(min_year=2015))

    assert [r.openalex_id for r in selected] == ["W2"]


def test_a_query_needs_every_word_in_the_title() -> None:
    rows = [
        row(openalex_id="W1", title="Energy Harvesting Sensors"),
        row(openalex_id="W2", title="Energy Efficiency"),
    ]

    selected = reading_list.select(rows, reading_list.Selector(query="energy harvest"))

    assert [r.openalex_id for r in selected] == ["W1"]


def test_an_empty_selector_selects_the_whole_pending_set() -> None:
    assert reading_list.Selector().empty
    assert not reading_list.Selector(via=("citation",)).empty


def test_the_note_is_a_sibling_of_the_papers_folder(tmp_path: Path) -> None:
    """`papers_dir.glob("*.md")` is walked by half the commands in the CLI."""
    papers = tmp_path / "papers"

    assert reading_list.path_for(papers) == tmp_path / "Reading List.md"


def test_rendering_the_same_ledger_twice_is_byte_identical(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    rows = [row(openalex_id="W1", seeds=("W8",))]

    first = reading_list.write(papers, rows, notes={"W8": "Mementos"})
    before = first.read_bytes()
    reading_list.write(papers, rows, notes={"W8": "Mementos"})

    assert first.read_bytes() == before
