"""The screening ledger: the record of what was looked at and turned down.

Pure file I/O and a fold, so nothing here needs a client. What is pinned is the
TSV's safety without quoting, last-row-wins, and that the module only ever
appends.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from research_assistant import screening

if TYPE_CHECKING:
    from pathlib import Path


def row(**kwargs: object) -> screening.Decision:
    fields: dict[str, object] = {
        "decided": "2026-09-04T11:20:03+00:00",
        "decision": screening.EXCLUDE,
    }
    fields.update(kwargs)
    return screening.Decision(**fields)  # type: ignore[arg-type]


def test_a_title_containing_a_tab_is_flattened_to_one_space() -> None:
    """No quoting, so the file stays greppable and one row stays one line."""
    line = screening.format_row(row(title="A\tsurvey\nof\r\nthings"))

    assert line.count("\t") == len(screening.HEADER) - 1
    assert line.endswith("A survey of things\n")


def test_a_long_title_is_truncated_rather_than_wrapped() -> None:
    line = screening.format_row(row(title="x" * 500))

    assert len(line.split("\t")[-1].rstrip("\n")) == 200


def test_a_row_with_extra_tabs_rejoins_them_into_the_title() -> None:
    """Column order is the escaping strategy: damage lands in the readable one."""
    parsed = screening.parse_row(
        "2026-09-04T11:20:03+00:00\texclude\tW1\t10.1/a\t2017\t\t\tsurvey\tA\tsplit\ttitle"
    )

    assert parsed is not None
    assert parsed.title == "A\tsplit\ttitle"
    assert parsed.reason == "survey"


def test_a_short_row_is_padded_not_rejected() -> None:
    parsed = screening.parse_row("2026-09-04T11:20:03+00:00\tinclude\tW1")

    assert parsed is not None
    assert parsed.openalex_id == "W1"
    assert parsed.title == ""


def test_a_malformed_row_is_counted_not_raised(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    screening.append(papers, [row(openalex_id="W1")])
    with screening.ledger_path(papers).open("a", encoding="utf-8") as handle:
        handle.write("this is not a row at all\n")
        handle.write("2026-01-01T00:00:00+00:00\tnonsense\tW9\n")

    ledger = screening.load(papers)

    assert ledger.unreadable == 2
    assert len(ledger.rows) == 1


def test_the_last_row_for_an_id_is_the_standing_decision(tmp_path: Path) -> None:
    """A change of mind is a new row, never a rewrite."""
    papers = tmp_path / "papers"
    papers.mkdir()
    screening.append(papers, [row(openalex_id="W1", decision=screening.EXCLUDE)])
    screening.append(
        papers,
        [
            row(
                openalex_id="W1",
                decision=screening.INCLUDE,
                decided="2026-10-01T00:00:00+00:00",
            )
        ],
    )

    ledger = screening.load(papers)

    found = ledger.lookup(openalex_id="W1", doi=None)
    assert found is not None
    assert found.decision == screening.INCLUDE
    assert len(ledger.rows) == 2


def test_a_pending_row_does_not_suppress_a_candidate(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    screening.append(papers, [row(openalex_id="W1", decision=screening.PENDING)])

    ledger = screening.load(papers)

    assert ledger.lookup(openalex_id="W1", doi=None) is not None
    assert not ledger.decided(openalex_id="W1", doi=None)


def test_an_exclusion_stands_for_either_identifier(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    screening.append(papers, [row(openalex_id="W1", doi="10.1145/ABC")])

    ledger = screening.load(papers)

    assert ledger.decided(openalex_id="W1", doi=None)
    assert ledger.decided(openalex_id=None, doi="10.1145/abc")
    assert ledger.decided(openalex_id=None, doi="10.1145/ABC")  # case folded
    assert not ledger.decided(openalex_id="W2", doi="10.1/other")


def test_append_never_opens_the_file_for_writing(tmp_path: Path) -> None:
    """The append-only property, stated as something testable."""
    papers = tmp_path / "papers"
    papers.mkdir()
    screening.append(papers, [row(openalex_id="W1")])
    before = screening.ledger_path(papers).read_bytes()

    screening.append(papers, [row(openalex_id="W2")])

    after = screening.ledger_path(papers).read_bytes()
    assert after.startswith(before)


def test_a_new_ledger_carries_its_version_line_and_header(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()

    screening.append(papers, [row(openalex_id="W1")])

    lines = screening.ledger_path(papers).read_text(encoding="utf-8").splitlines()
    assert lines[0] == screening.VERSION_LINE
    assert lines[1].split("\t") == list(screening.HEADER)


def test_the_ledger_is_a_sibling_of_the_papers_folder(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()

    assert screening.ledger_path(papers) == tmp_path / "screening.tsv"


def test_a_ledger_inside_the_papers_folder_is_an_error(tmp_path: Path) -> None:
    """Obsidian would show it among the notes, and nothing would read it."""
    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / screening.SCREENING_FILENAME).write_text("", encoding="utf-8")

    try:
        screening.load(papers)
    except screening.ScreeningError as exc:
        assert "sibling" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected a ScreeningError")


def test_a_missing_ledger_reads_as_empty(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()

    ledger = screening.load(papers)

    assert ledger.rows == ()
    assert not ledger.decided(openalex_id="W1", doi=None)


def test_counts_fold_a_work_recorded_under_both_identifiers(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    screening.append(
        papers,
        [
            row(openalex_id="W1", doi="10.1/a", decision=screening.INCLUDE),
            row(openalex_id="W2", decision=screening.EXCLUDE),
        ],
    )

    tally = screening.counts(screening.load(papers))

    assert tally[screening.INCLUDE] == 1
    assert tally[screening.EXCLUDE] == 1
