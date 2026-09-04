"""The command layer: tag arithmetic, key allocation, and what `near` counts.

`expand`, `relink` and `tidy` had no tests at all, which is why the defects
pinned here survived. No network: the pure helpers are called directly and the
read-only commands run against a temp vault through Typer's runner.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from notes import write_note
from typer.testing import CliRunner

from research_assistant import cli, graph, health, screening, search, sources, vault

runner = CliRunner()


def test_expand_does_not_retract_the_read_tag_from_a_root() -> None:
    """`read` is derived from the body, so re-tagging a root must not clear it."""
    tags = cli._tags_for(
        ("hardware",), harvested=False, existing=["paper", "harvested", "read"]
    )

    assert "read" in tags
    assert "seed" in tags
    assert "harvested" not in tags


def test_tags_for_owns_provenance_and_topics_but_nothing_else() -> None:
    tags = cli._tags_for(
        ("energy",), harvested=True, existing=["paper", "topic/stale", "mine"]
    )

    assert tags == ["paper", "harvested", "topic/energy", "mine"]


def test_a_new_note_takes_a_suffix_when_the_vault_already_claims_its_key() -> None:
    assert cli._unclaimed_key("yen2023soil", {"yen2023soil"}) == "yen2023soila"
    assert cli._unclaimed_key("yen2023soil", set()) == "yen2023soil"


def test_near_counts_each_unresolved_reference_once(tmp_path: Path) -> None:
    """`record.cites` already holds the unresolved names; adding them double-counts."""
    papers = tmp_path / "papers"
    write_note(papers, "Mementos", key="ransford2011mementos")
    write_note(papers, "ALFRED", key="maioli2021alfred", cites=("Mementos", "Ghost"))

    result = runner.invoke(
        cli.app, ["--papers-dir", str(papers), "near", "maioli2021alfred"]
    )

    assert result.exit_code == 0
    assert "1 of 2 unresolved" in result.stdout


class _NoClient:
    """Stands in for ``httpx.Client`` where the fetch itself is already faked."""

    def __enter__(self) -> _NoClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Commands open a client before deciding whether they need one."""
    monkeypatch.setattr(httpx, "Client", lambda **_kwargs: _NoClient())


def _candidate(ident: str, doi: str | None = None) -> graph.Candidate:
    return graph.Candidate(
        openalex_id=ident,
        doi=doi,
        provenance=frozenset({graph.REFERENCE}),
        seeds=frozenset({"W1"}),
    )


def _work(
    ident: str, *, year: int | None = 2020, doi: str | None = None
) -> dict[str, Any]:
    work: dict[str, Any] = {"id": f"https://openalex.org/{ident}", "title": ident}
    if year is not None:
        work["publication_year"] = year
    if doi is not None:
        work["doi"] = f"https://doi.org/{doi}"
    return work


def test_a_harvested_note_is_not_a_root(tmp_path: Path) -> None:
    """Otherwise re-running `expand` quietly reaches depth 2."""
    papers = tmp_path / "papers"
    write_note(papers, "Seed", key="a2020seed", doi="10.1/a", tags=("paper", "seed"))
    write_note(
        papers, "Grown", key="b2020grown", doi="10.1/b", tags=("paper", "harvested")
    )

    roots = cli._root_notes(papers)

    assert [path.stem for path, _ in roots] == ["Seed"]


def test_a_note_with_no_identifier_is_not_a_root(tmp_path: Path) -> None:
    """The graph cannot walk from a note it cannot look up."""
    papers = tmp_path / "papers"
    write_note(papers, "Datasheet", key="acme2020datasheet")

    assert cli._root_notes(papers) == []


def test_select_drops_a_work_whose_doi_is_already_in_the_vault() -> None:
    """A backward candidate carries no DOI, so only the fetched work reveals it."""
    fresh = {"W2": _candidate("W2")}
    works = {"W2": _work("W2", doi="10.1145/known")}

    kept = cli._select(fresh, works, {"10.1145/known": Path("Known.md")}, min_year=None)

    assert kept == []


def test_select_drops_a_work_with_no_year_when_a_floor_is_given() -> None:
    fresh = {"W2": _candidate("W2"), "W3": _candidate("W3")}
    works = {"W2": _work("W2", year=None), "W3": _work("W3", year=2021)}

    kept = cli._select(fresh, works, {}, min_year=2015)

    assert [c.openalex_id for c in kept] == ["W3"]


def test_select_sorts_newest_first_and_leaves_the_cap_to_the_caller() -> None:
    """`--limit` defers rather than decides, so a deferred paper gets no row."""
    fresh = {ident: _candidate(ident) for ident in ("W2", "W3", "W4")}
    works = {
        "W2": _work("W2", year=2015),
        "W3": _work("W3", year=2024),
        "W4": _work("W4", year=2019),
    }

    kept = cli._select(fresh, works, {}, min_year=None)

    assert [c.openalex_id for c in kept] == ["W3", "W4", "W2"]


def test_select_skips_a_candidate_openalex_no_longer_returns() -> None:
    """A merged or deleted work simply does not come back in the batch."""
    kept = cli._select({"W9": _candidate("W9")}, {}, {}, min_year=None)

    assert kept == []


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.status_code = 200
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class _FakeClient:
    def __init__(self, results: list[dict[str, object]]) -> None:
        self._results = results

    def get(self, url: str, **_kwargs: object) -> _FakeResponse:
        return _FakeResponse({"results": self._results, "meta": {}})


def test_a_doi_recorded_in_caps_still_backfills_its_openalex_id(
    tmp_path: Path,
) -> None:
    """`_work_doi` lowercases, so the lookup table has to as well."""
    papers = tmp_path / "papers"
    note = write_note(papers, "Mementos", key="ransford2011mementos", doi="10.1145/ABC")
    client = _FakeClient(
        [{"id": "https://openalex.org/W42", "doi": "https://doi.org/10.1145/abc"}]
    )

    repaired = cli._backfill_openalex_ids(papers, client=client)  # type: ignore[arg-type]

    assert repaired == 1
    assert vault.read_frontmatter(note)["openalex_id"] == "W42"


def test_relink_links_only_to_notes_that_exist_and_never_to_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: None
) -> None:
    """An unresolved reference is a gap in the vault, not a broken wikilink."""
    papers = tmp_path / "papers"
    write_note(papers, "Alfred", key="maioli2021alfred")
    write_note(papers, "Mementos", key="ransford2011mementos")
    vault.update_frontmatter(papers / "Alfred.md", {"openalex_id": "W1"})
    vault.update_frontmatter(papers / "Mementos.md", {"openalex_id": "W2"})
    works = [
        {
            "id": "https://openalex.org/W1",
            "referenced_works": [
                "https://openalex.org/W2",  # in the vault
                "https://openalex.org/W1",  # itself
                "https://openalex.org/W9",  # not in the vault
            ],
            "topics": [],
        },
        {"id": "https://openalex.org/W2", "referenced_works": [], "topics": []},
    ]
    monkeypatch.setattr(graph, "fetch_works", lambda *a, **k: works)

    result = runner.invoke(cli.app, ["--papers-dir", str(papers), "relink"])

    assert result.exit_code == 0
    assert vault.read_frontmatter(papers / "Alfred.md")["cites"] == ["[[Mementos]]"]


def test_relink_refreshes_the_counts_it_already_fetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: None
) -> None:
    """`find --min-citations` filtered on a snapshot from import time."""
    papers = tmp_path / "papers"
    note = write_note(papers, "Alfred", key="maioli2021alfred", citations=10)
    vault.update_frontmatter(note, {"openalex_id": "W1"})
    works = [
        {
            "id": "https://openalex.org/W1",
            "referenced_works": [],
            "topics": [],
            "cited_by_count": 350,
            "open_access": {"oa_status": "Gold"},
            "best_oa_location": {"pdf_url": "https://example.org/a.pdf"},
        }
    ]
    monkeypatch.setattr(graph, "fetch_works", lambda *a, **k: works)

    runner.invoke(cli.app, ["--papers-dir", str(papers), "relink"])

    front = vault.read_frontmatter(note)
    assert front["citations"] == 350
    assert front["open_access"] == "gold"
    assert front["pdf_url"] == "https://example.org/a.pdf"


def test_relink_does_not_blank_a_pdf_url_openalex_lacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: None
) -> None:
    papers = tmp_path / "papers"
    note = write_note(
        papers, "Alfred", key="maioli2021alfred", pdf_url="https://mine.example/a.pdf"
    )
    vault.update_frontmatter(note, {"openalex_id": "W1"})
    monkeypatch.setattr(
        graph,
        "fetch_works",
        lambda *a, **k: [
            {"id": "https://openalex.org/W1", "referenced_works": [], "topics": []}
        ],
    )

    runner.invoke(cli.app, ["--papers-dir", str(papers), "relink"])

    assert vault.read_frontmatter(note)["pdf_url"] == "https://mine.example/a.pdf"


def test_a_topic_one_paper_carries_earns_no_hub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: None
) -> None:
    """A label on one paper of two hundred is graph hair, not a cluster."""
    papers = tmp_path / "papers"
    write_note(papers, "Alfred", key="maioli2021alfred")
    write_note(papers, "Mementos", key="ransford2011mementos")
    vault.update_frontmatter(papers / "Alfred.md", {"openalex_id": "W1"})
    vault.update_frontmatter(papers / "Mementos.md", {"openalex_id": "W2"})
    topic = {"display_name": "Shared", "subfield": {"display_name": "Hardware"}}
    lonely = {"display_name": "Lonely", "subfield": {"display_name": "Hardware"}}
    works = [
        {"id": "https://openalex.org/W1", "topics": [topic, lonely], "cites": []},
        {"id": "https://openalex.org/W2", "topics": [topic], "cites": []},
    ]
    monkeypatch.setattr(graph, "fetch_works", lambda *a, **k: works)

    runner.invoke(cli.app, ["--papers-dir", str(papers), "relink"])

    hubs = sorted(p.stem for p in (tmp_path / "topics").glob("*.md"))
    assert hubs == ["Shared"]


def test_tidy_derives_the_read_tag_both_ways(tmp_path: Path, offline: None) -> None:
    """`read` states a fact about the note, so emptying it retracts the tag."""
    papers = tmp_path / "papers"
    note = write_note(papers, "Alfred", key="maioli2021alfred")
    note.write_text(
        note.read_text(encoding="utf-8") + "A thought of my own.\n", encoding="utf-8"
    )

    runner.invoke(cli.app, ["--papers-dir", str(papers), "tidy", "--no-abstracts"])
    assert "read" in vault.read_frontmatter(note)["tags"]

    body = note.read_text(encoding="utf-8").replace("A thought of my own.\n", "")
    note.write_text(body, encoding="utf-8")

    runner.invoke(cli.app, ["--papers-dir", str(papers), "tidy", "--no-abstracts"])
    assert "read" not in vault.read_frontmatter(note)["tags"]


def test_tidy_adopts_a_hand_saved_pdf_only_when_the_note_claims_none(
    tmp_path: Path, offline: None
) -> None:
    papers = tmp_path / "papers"
    note = write_note(papers, "Alfred", key="maioli2021alfred")
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    (pdfs / "maioli2021alfred.pdf").write_bytes(b"%PDF-1.4\n")

    runner.invoke(cli.app, ["--papers-dir", str(papers), "tidy", "--no-abstracts"])

    assert vault.read_frontmatter(note)["pdf"] == "[[maioli2021alfred.pdf]]"


def test_fix_writes_the_retracted_key_and_a_second_run_is_a_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: None
) -> None:
    papers = tmp_path / "papers"
    note = write_note(papers, "Withdrawn", key="mehra2020hydro", doi="10.1/r")
    report = health.Report(
        checked=1,
        unchecked=(),
        retracted=((search.load(papers)[0], "retraction"),),
        corrections=(),
        drift=(),
        duplicates=(),
    )
    monkeypatch.setattr(health, "check", lambda *a, **k: report)

    first = runner.invoke(cli.app, ["--papers-dir", str(papers), "health", "--fix"])
    assert first.exit_code == 1  # a finding is a finding, fixed or not
    assert vault.read_frontmatter(note)["retracted"] == "retraction"
    assert "Set `retracted` on 1 note(s)" in first.stdout

    second = runner.invoke(cli.app, ["--papers-dir", str(papers), "health", "--fix"])
    assert "Set `retracted` on 0 note(s)" in second.stdout


def test_a_withdrawn_notice_sets_retracted_back_to_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: None
) -> None:
    """Derived, not claimed: nothing has to keep the key true by hand."""
    papers = tmp_path / "papers"
    note = write_note(
        papers, "Reinstated", key="a2020one", doi="10.1/r", retracted="retraction"
    )
    clean = health.Report(
        checked=1, unchecked=(), retracted=(), corrections=(), drift=(), duplicates=()
    )
    monkeypatch.setattr(health, "check", lambda *a, **k: clean)

    result = runner.invoke(cli.app, ["--papers-dir", str(papers), "health", "--fix"])

    assert result.exit_code == 0
    assert vault.read_frontmatter(note)["retracted"] is None


def _report_count(output: str, label: str) -> int:
    """The number on a `--report` reconciliation line, ignoring its padding."""
    for line in output.splitlines():
        if label in line:
            return int(line.rsplit(maxsplit=1)[-1])
    raise AssertionError(f"{label!r} not in report")


def test_a_note_that_exists_beats_an_exclude_row(tmp_path: Path) -> None:
    """Adding it *was* the change of mind, so the note wins and --report says so."""
    papers = tmp_path / "papers"
    note = write_note(papers, "Alfred", key="maioli2021alfred", doi="10.1/a")
    vault.update_frontmatter(note, {"openalex_id": "W1"})
    screening.append(
        papers,
        [
            screening.Decision(
                decided=screening.now(),
                decision=screening.EXCLUDE,
                openalex_id="W1",
                doi="10.1/a",
                reason="thought better of it",
            )
        ],
    )

    result = runner.invoke(cli.app, ["--papers-dir", str(papers), "expand", "--report"])

    assert result.exit_code == 0
    assert _report_count(result.stdout, "excluded, but a note exists") == 1


def test_report_names_the_notes_deleted_by_hand(tmp_path: Path) -> None:
    """The deletion is the decision; the ledger's job is only not to undo it."""
    papers = tmp_path / "papers"
    papers.mkdir(parents=True)
    screening.append(
        papers,
        [
            screening.Decision(
                decided=screening.now(),
                decision=screening.INCLUDE,
                openalex_id="W9",
                doi="10.1/gone",
                reason="adopted",
            )
        ],
    )

    result = runner.invoke(cli.app, ["--papers-dir", str(papers), "expand", "--report"])

    assert _report_count(result.stdout, "included, but no note exists") == 1
    assert "--exclude W9" in result.stdout
    assert "deleted by hand" in result.stdout


def test_adopt_writes_one_row_per_note_and_refuses_twice(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    a = write_note(papers, "Alfred", key="maioli2021alfred", doi="10.1/a")
    write_note(papers, "Datasheet", key="acme2020ds")  # no identifier, skipped
    vault.update_frontmatter(a, {"openalex_id": "W1"})

    first = runner.invoke(cli.app, ["--papers-dir", str(papers), "expand", "--adopt"])
    second = runner.invoke(cli.app, ["--papers-dir", str(papers), "expand", "--adopt"])

    assert first.exit_code == 0
    assert second.exit_code == 1
    ledger = screening.load(papers)
    assert len(ledger.rows) == 1
    assert ledger.rows[0].reason == "adopted"


def test_excluding_needs_a_reason(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir(parents=True)

    result = runner.invoke(
        cli.app, ["--papers-dir", str(papers), "expand", "--exclude", "W1"]
    )

    assert result.exit_code == 1
    assert "needs --reason" in result.output


def test_recording_an_exclusion_then_including_it_again(tmp_path: Path) -> None:
    """Un-excluding is a new row, which is what makes the file append-only."""
    papers = tmp_path / "papers"
    papers.mkdir(parents=True)

    runner.invoke(
        cli.app,
        [
            "--papers-dir",
            str(papers),
            "expand",
            "--exclude",
            "W1",
            "--reason",
            "survey",
        ],
    )
    assert screening.load(papers).decided(openalex_id="W1", doi=None)

    runner.invoke(cli.app, ["--papers-dir", str(papers), "expand", "--include", "W1"])

    ledger = screening.load(papers)
    found = ledger.lookup(openalex_id="W1", doi=None)
    assert found is not None
    assert found.decision == screening.INCLUDE
    assert len(ledger.rows) == 2


def test_only_one_terminal_mode_at_a_time(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir(parents=True)

    result = runner.invoke(
        cli.app, ["--papers-dir", str(papers), "expand", "--report", "--adopt"]
    )

    assert result.exit_code == 1


def test_cited_keys_reads_every_cite_command_and_splits_on_commas() -> None:
    tex = r"""
    \cite{alpha2020one}
    \parencite[see][12]{beta2021two,gamma2022three}
    \textcite*{delta2023four}
    \footcite{epsilon2024five}
    """

    assert cli.cited_keys(tex) == {
        "alpha2020one",
        "beta2021two",
        "gamma2022three",
        "delta2023four",
        "epsilon2024five",
    }


def test_cited_keys_reads_bib_entry_keys() -> None:
    bib = "@article{alpha2020one,\n  title = {A}\n}\n@inproceedings{ beta2021two ,\n}"

    assert cli.cited_keys(bib, bib=True) == {"alpha2020one", "beta2021two"}


def test_cite_check_fails_on_a_key_with_no_note(tmp_path: Path) -> None:
    """The real error: a citation the bibliography cannot resolve."""
    papers = tmp_path / "papers"
    write_note(papers, "Alfred", key="maioli2021alfred")
    tex = tmp_path / "thesis.tex"
    tex.write_text(r"\cite{maioli2021alfred,ransford2011typo}", encoding="utf-8")

    result = runner.invoke(
        cli.app, ["--papers-dir", str(papers), "cite-check", str(tex)]
    )

    assert result.exit_code == 1
    assert "ransford2011typo" in result.stdout
    assert "1 with no note" in result.stdout


def test_cite_check_passes_when_every_key_resolves(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    write_note(papers, "Alfred", key="maioli2021alfred")
    write_note(papers, "Mementos", key="ransford2011mementos")
    tex = tmp_path / "thesis.tex"
    tex.write_text(r"\cite{maioli2021alfred}", encoding="utf-8")

    result = runner.invoke(
        cli.app, ["--papers-dir", str(papers), "cite-check", str(tex)]
    )

    assert result.exit_code == 0
    assert "1 note(s) cited nowhere" in result.stdout
    assert "ransford2011mementos" in result.stdout


def test_cite_check_resolves_by_cite_key_not_filename(tmp_path: Path) -> None:
    """Notes are named for their title, so `path.stem` is the wrong join."""
    papers = tmp_path / "papers"
    write_note(papers, "A Long Descriptive Title", key="maioli2021alfred")
    tex = tmp_path / "thesis.tex"
    tex.write_text(r"\cite{maioli2021alfred}", encoding="utf-8")

    result = runner.invoke(
        cli.app,
        ["--papers-dir", str(papers), "cite-check", str(tex), "--no-unused"],
    )

    assert result.exit_code == 0


def test_bib_check_reports_a_cite_key_claimed_twice(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    write_note(papers, "One paper", key="lovelace2020twin")
    write_note(papers, "Another paper entirely", key="lovelace2020twin")

    result = runner.invoke(cli.app, ["--papers-dir", str(papers), "bib", "--check"])

    assert result.exit_code == 1
    assert "1 collision" in result.stdout
    assert "lovelace2020twin" in result.stdout


def test_bib_check_is_quiet_on_a_clean_vault(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    write_note(papers, "One paper", key="lovelace2020one")
    write_note(papers, "Another paper", key="turing2019another")

    result = runner.invoke(cli.app, ["--papers-dir", str(papers), "bib", "--check"])

    assert result.exit_code == 0
    assert "0 collision(s)" in result.stdout


def test_bib_refuses_to_write_over_a_collision(tmp_path: Path) -> None:
    """The generator does not invent a key; it names the notes and stops."""
    papers = tmp_path / "papers"
    write_note(papers, "One paper", key="lovelace2020twin")
    write_note(papers, "Another paper entirely", key="lovelace2020twin")
    out = tmp_path / "refs.bib"

    result = runner.invoke(
        cli.app, ["--papers-dir", str(papers), "bib", "--out", str(out)]
    )

    assert result.exit_code == 1
    assert not out.exists()


def test_bib_wants_exactly_one_of_out_and_check(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    write_note(papers, "One paper", key="lovelace2020one")

    result = runner.invoke(cli.app, ["--papers-dir", str(papers), "bib"])

    assert result.exit_code == 1


def _pending(**kwargs: object) -> screening.Decision:
    fields: dict[str, object] = {
        "decided": "2026-09-04T11:20:03+00:00",
        "decision": screening.PENDING,
    }
    fields.update(kwargs)
    return screening.Decision(**fields)  # type: ignore[arg-type]


def _fake_graph(
    monkeypatch: pytest.MonkeyPatch,
    candidates: dict[str, graph.Candidate],
    works: dict[str, dict[str, Any]],
) -> None:
    """One hop out from one root, without a network."""
    monkeypatch.setattr(
        graph, "fetch_by_doi", lambda _dois, *, client: [_work("W1", year=2010)]
    )
    monkeypatch.setattr(graph, "harvest", lambda _seeds, **_kwargs: candidates)
    monkeypatch.setattr(
        graph,
        "fetch_works",
        lambda ids, *, client, **_kwargs: [
            works[ident] for ident in ids if ident in works
        ],
    )


def _seeded(ident: str, *, seeds: tuple[str, ...]) -> graph.Candidate:
    return graph.Candidate(
        openalex_id=ident,
        doi=None,
        provenance=frozenset({graph.CITATION}),
        seeds=frozenset(seeds),
    )


def test_expand_records_pending_rows_and_writes_no_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: None
) -> None:
    """The whole point: the papers folder only grows when you say `promote`."""
    papers = tmp_path / "papers"
    write_note(papers, "Seed", key="a2010seed", doi="10.1/a")
    _fake_graph(
        monkeypatch,
        {"W2": _seeded("W2", seeds=("W1", "W9"))},
        {"W2": _work("W2", year=2021)},
    )

    result = runner.invoke(cli.app, ["--papers-dir", str(papers), "expand"])

    assert result.exit_code == 0
    assert sorted(p.name for p in papers.glob("*.md")) == ["Seed.md"]
    rows = screening.load(papers).rows
    assert [(r.decision, r.openalex_id) for r in rows] == [("pending", "W2")]
    assert (tmp_path / "Reading List.md").is_file()
    assert "1 pending (+1)" in result.stdout


def test_the_floor_keeps_two_seeds_or_a_famous_paper_and_drops_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: None
) -> None:
    """Two independent signals: reached by several of yours, or widely cited."""
    papers = tmp_path / "papers"
    write_note(papers, "Seed", key="a2010seed", doi="10.1/a")
    works = {
        "W2": _work("W2", year=2021) | {"cited_by_count": 5},
        "W3": _work("W3", year=2021) | {"cited_by_count": 900},
        "W4": _work("W4", year=2021),
    }
    _fake_graph(
        monkeypatch,
        {
            "W2": _seeded("W2", seeds=("W1",)),
            "W3": _seeded("W3", seeds=("W1",)),
            "W4": _seeded("W4", seeds=("W1", "W9")),
        },
        works,
    )

    result = runner.invoke(cli.app, ["--papers-dir", str(papers), "expand"])

    assert result.exit_code == 0
    assert "1 below the floor (<2 seeds and <50 citations)" in result.stdout
    assert {r.openalex_id for r in screening.load(papers).rows} == {"W3", "W4"}


def test_a_lowered_floor_records_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: None
) -> None:
    papers = tmp_path / "papers"
    write_note(papers, "Seed", key="a2010seed", doi="10.1/a")
    _fake_graph(monkeypatch, {"W2": _seeded("W2", seeds=("W1",))}, {"W2": _work("W2")})

    result = runner.invoke(
        cli.app,
        [
            "--papers-dir",
            str(papers),
            "expand",
            "--min-seeds",
            "1",
            "--min-citations",
            "0",
        ],
    )

    assert result.exit_code == 0
    assert len(screening.load(papers).rows) == 1


def test_a_second_expand_records_nothing_and_re_renders_the_same_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: None
) -> None:
    """A pending row suppresses a new row, so the ledger stops doubling."""
    papers = tmp_path / "papers"
    write_note(papers, "Seed", key="a2010seed", doi="10.1/a")
    _fake_graph(
        monkeypatch,
        {"W2": _seeded("W2", seeds=("W1", "W9"))},
        {"W2": _work("W2", year=2021)},
    )
    runner.invoke(cli.app, ["--papers-dir", str(papers), "expand"])
    before = (tmp_path / "Reading List.md").read_bytes()

    result = runner.invoke(cli.app, ["--papers-dir", str(papers), "expand"])

    assert result.exit_code == 0
    assert "1 already screened" in result.stdout
    assert len(screening.load(papers).rows) == 1
    assert (tmp_path / "Reading List.md").read_bytes() == before


def test_a_dry_run_writes_no_row_and_no_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: None
) -> None:
    papers = tmp_path / "papers"
    write_note(papers, "Seed", key="a2010seed", doi="10.1/a")
    _fake_graph(
        monkeypatch,
        {"W2": _seeded("W2", seeds=("W1", "W9"))},
        {"W2": _work("W2", year=2021)},
    )

    result = runner.invoke(
        cli.app, ["--papers-dir", str(papers), "expand", "--dry-run"]
    )

    assert result.exit_code == 0
    assert not screening.ledger_path(papers).exists()
    assert not (tmp_path / "Reading List.md").exists()


def test_a_selector_excludes_a_whole_route_after_confirming(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    screening.append(
        papers,
        [
            _pending(openalex_id="W2", via=("citation",), title="Forward"),
            _pending(openalex_id="W3", via=("reference",), title="Backward"),
        ],
    )

    result = runner.invoke(
        cli.app,
        [
            "--papers-dir",
            str(papers),
            "expand",
            "--exclude",
            "--via",
            "citation",
            "--reason",
            "all NVM, not harvesting",
        ],
        input="y\n",
    )

    assert result.exit_code == 0
    assert "1 candidate(s) selected" in result.stdout
    ledger = screening.load(papers)
    assert ledger.decided(openalex_id="W2", doi=None)
    assert [r.openalex_id for r in screening.pending(ledger)] == ["W3"]


def test_a_declined_selector_writes_nothing(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    screening.append(papers, [_pending(openalex_id="W2", via=("citation",))])

    result = runner.invoke(
        cli.app,
        [
            "--papers-dir",
            str(papers),
            "expand",
            "--exclude",
            "--via",
            "citation",
            "--reason",
            "no",
        ],
        input="n\n",
    )

    assert result.exit_code == 0
    assert len(screening.load(papers).rows) == 1
    assert not (tmp_path / "Reading List.md").exists()


def test_a_selector_matching_nothing_is_an_error(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    screening.append(papers, [_pending(openalex_id="W2", via=("reference",))])

    result = runner.invoke(
        cli.app,
        [
            "--papers-dir",
            str(papers),
            "expand",
            "--exclude",
            "--via",
            "citation",
            "--reason",
            "no",
        ],
    )

    assert result.exit_code == 1
    assert "matches no pending candidate" in result.output


def _promotable(ident: str = "W2", *, pdf: bool = True) -> dict[str, Any]:
    work: dict[str, Any] = {
        "id": f"https://openalex.org/{ident}",
        "title": "Mementos: System Support",
        "publication_year": 2011,
        "cited_by_count": 812,
        "authorships": [{"author": {"display_name": "Benjamin Ransford"}}],
        "primary_location": {"source": {"display_name": "ASPLOS"}},
        "topics": [],
    }
    if pdf:
        work["best_oa_location"] = {"pdf_url": "https://example.org/m.pdf"}
    return work


def test_promote_writes_the_note_the_pdf_and_an_include_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: None
) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    screening.append(
        papers, [_pending(openalex_id="W2", title="Mementos", seeds=("W1",))]
    )
    monkeypatch.setattr(graph, "fetch_works", lambda *a, **k: [_promotable()])
    monkeypatch.setattr(sources, "fetch_pdf", lambda *a, **k: b"%PDF-1.4 body")

    result = runner.invoke(cli.app, ["--papers-dir", str(papers), "promote", "W2"])

    assert result.exit_code == 0
    assert len(list(papers.glob("*.md"))) == 1
    assert (tmp_path / "pdfs" / "ransford2011mementos.pdf").is_file()
    ledger = screening.load(papers)
    assert ledger.decided(openalex_id="W2", doi=None)
    assert screening.pending(ledger) == ()
    assert "0 pending (-1)" in result.stdout
    assert "Now run the `relink` command." in result.stdout


def test_promote_writes_the_note_even_when_no_pdf_can_be_had(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: None
) -> None:
    """`tidy` adopts the PDF once it lands; losing the note would be worse."""
    papers = tmp_path / "papers"
    papers.mkdir()
    screening.append(papers, [_pending(openalex_id="W2", title="Mementos")])
    monkeypatch.setattr(graph, "fetch_works", lambda *a, **k: [_promotable(pdf=False)])

    result = runner.invoke(cli.app, ["--papers-dir", str(papers), "promote", "W2"])

    assert result.exit_code == 0
    assert len(list(papers.glob("*.md"))) == 1
    assert not (tmp_path / "pdfs").exists()
    assert "no fetchable PDF" in result.stdout


def test_promote_with_no_pdf_flag_never_reaches_for_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: None
) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    screening.append(papers, [_pending(openalex_id="W2", title="Mementos")])
    monkeypatch.setattr(graph, "fetch_works", lambda *a, **k: [_promotable()])

    def _refuse(*_a: object, **_k: object) -> bytes:
        raise AssertionError("--no-pdf must not fetch")

    monkeypatch.setattr(sources, "fetch_pdf", _refuse)

    result = runner.invoke(
        cli.app, ["--papers-dir", str(papers), "promote", "W2", "--no-pdf"]
    )

    assert result.exit_code == 0
    assert not (tmp_path / "pdfs").exists()


def test_promote_a_paper_already_in_the_vault_creates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: None
) -> None:
    """Vault and ledger agree: the row is written, the note is not duplicated."""
    papers = tmp_path / "papers"
    write_note(papers, "Mementos", key="ransford2011mementos")
    vault.update_frontmatter(papers / "Mementos.md", {"openalex_id": "W2"})
    screening.append(papers, [_pending(openalex_id="W2", title="Mementos")])
    monkeypatch.setattr(graph, "fetch_works", lambda *a, **k: [_promotable()])

    result = runner.invoke(cli.app, ["--papers-dir", str(papers), "promote", "W2"])

    assert result.exit_code == 0
    assert "Already in the vault" in result.stdout
    assert len(list(papers.glob("*.md"))) == 1
    assert screening.load(papers).decided(openalex_id="W2", doi=None)


def test_promote_refuses_to_guess_between_two_matching_titles(
    tmp_path: Path,
) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    screening.append(
        papers,
        [
            _pending(openalex_id="W2", title="A Survey of Energy Harvesting"),
            _pending(openalex_id="W3", title="Energy-Aware Scheduling"),
        ],
    )

    result = runner.invoke(cli.app, ["--papers-dir", str(papers), "promote", "energy"])

    assert result.exit_code == 1
    assert "2 pending candidates match 'energy'" in result.output


def test_promoting_the_same_id_twice_reports_the_note_and_creates_nothing(
    tmp_path: Path,
) -> None:
    """The row has left the pending set precisely because the first run worked."""
    papers = tmp_path / "papers"
    write_note(papers, "Mementos", key="ransford2011mementos")
    vault.update_frontmatter(papers / "Mementos.md", {"openalex_id": "W2"})
    screening.append(papers, [_pending(openalex_id="W2", decision=screening.INCLUDE)])

    result = runner.invoke(cli.app, ["--papers-dir", str(papers), "promote", "W2"])

    assert result.exit_code == 0
    assert "Already in the vault" in result.stdout
    assert len(list(papers.glob("*.md"))) == 1


def test_promote_a_target_that_names_nothing_pending(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()

    result = runner.invoke(cli.app, ["--papers-dir", str(papers), "promote", "W9"])

    assert result.exit_code == 1
    assert "matches no pending candidate" in result.output


def test_reading_list_filters_what_it_prints_not_what_it_writes(
    tmp_path: Path,
) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    screening.append(
        papers,
        [
            _pending(openalex_id="W2", via=("citation",), title="Forward"),
            _pending(openalex_id="W3", via=("reference",), title="Backward"),
        ],
    )

    result = runner.invoke(
        cli.app, ["--papers-dir", str(papers), "reading-list", "--via", "citation"]
    )

    assert result.exit_code == 0
    assert "2 pending · showing 1" in result.stdout
    note = (tmp_path / "Reading List.md").read_text(encoding="utf-8")
    assert "Forward" in note
    assert "Backward" in note


def test_the_obsidian_uri_percent_encodes_both_halves(tmp_path: Path) -> None:
    (tmp_path / ".obsidian").mkdir()
    papers = tmp_path / "papers"
    papers.mkdir()
    note = papers / "Mementos - System Support.md"
    note.write_text("", encoding="utf-8")

    uri = cli._obsidian_uri(note, papers_dir=papers)

    assert uri == (
        f"obsidian://open?vault={tmp_path.name}&file=papers%2FMementos+-+System+Support"
    )


def test_an_unidentifiable_vault_names_where_it_looked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guessing a vault name is worse than saying so, as `--papers-dir` holds."""
    monkeypatch.delenv("OBSIDIAN_VAULT", raising=False)
    papers = tmp_path / "papers"
    write_note(papers, "Mementos", key="ransford2011mementos")

    result = runner.invoke(
        cli.app,
        ["--papers-dir", str(papers), "open", "ransford2011mementos", "--note"],
    )

    assert result.exit_code == 1
    assert "OBSIDIAN_VAULT" in result.output


def test_open_a_pending_candidate_points_at_promote(tmp_path: Path) -> None:
    """`open` is a read verb: it must not write a note, a PDF and a row."""
    papers = tmp_path / "papers"
    papers.mkdir()
    screening.append(papers, [_pending(openalex_id="W2", title="Mementos")])

    result = runner.invoke(cli.app, ["--papers-dir", str(papers), "open", "W2"])

    assert result.exit_code == 1
    assert "research-assistant promote W2" in result.output


def test_open_launches_the_pdf_and_the_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".obsidian").mkdir()
    papers = tmp_path / "papers"
    write_note(
        papers, "Mementos", key="ransford2011mementos", pdf="ransford2011mementos.pdf"
    )
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    (pdfs / "ransford2011mementos.pdf").write_bytes(b"%PDF-1.4")
    launched: list[str] = []

    def _record(target: str) -> bool:
        launched.append(target)
        return True

    monkeypatch.setattr(cli, "_launch", _record)

    result = runner.invoke(
        cli.app, ["--papers-dir", str(papers), "open", "ransford2011mementos"]
    )

    assert result.exit_code == 0
    assert launched[0].endswith("ransford2011mementos.pdf")
    assert launched[1].startswith("obsidian://open?vault=")


def test_open_pdf_only_fails_when_there_is_no_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    papers = tmp_path / "papers"
    write_note(papers, "Mementos", key="ransford2011mementos")
    monkeypatch.setattr(cli, "_launch", lambda _target: True)

    result = runner.invoke(
        cli.app,
        ["--papers-dir", str(papers), "open", "ransford2011mementos", "--pdf"],
    )

    assert result.exit_code == 1
    assert "No PDF on disk" in result.output


def test_promote_all_confirms_before_recreating_the_note_explosion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: None
) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    screening.append(papers, [_pending(openalex_id="W2", title="Mementos")])
    monkeypatch.setattr(graph, "fetch_works", lambda *a, **k: [_promotable()])

    result = runner.invoke(
        cli.app, ["--papers-dir", str(papers), "promote", "--all"], input="n\n"
    )

    assert result.exit_code == 0
    assert "1 candidate(s) selected" in result.stdout
    assert list(papers.glob("*.md")) == []
    assert screening.pending(screening.load(papers))


def test_report_and_adopt_do_not_render_the_reading_list(tmp_path: Path) -> None:
    """`--adopt` writes only include rows, so the pending set is unchanged."""
    papers = tmp_path / "papers"
    write_note(papers, "Seed", key="a2010seed", doi="10.1/a")

    assert (
        runner.invoke(
            cli.app, ["--papers-dir", str(papers), "expand", "--adopt"]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            cli.app, ["--papers-dir", str(papers), "expand", "--report"]
        ).exit_code
        == 0
    )
    assert not (tmp_path / "Reading List.md").exists()


def test_open_falls_back_to_the_note_when_no_pdf_is_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The note is still openable, so only `open --pdf` fails."""
    (tmp_path / ".obsidian").mkdir()
    papers = tmp_path / "papers"
    write_note(
        papers,
        "Mementos",
        key="ransford2011mementos",
        pdf_url="https://example.org/m.pdf",
    )
    launched: list[str] = []

    def _record(target: str) -> bool:
        launched.append(target)
        return True

    monkeypatch.setattr(cli, "_launch", _record)

    result = runner.invoke(
        cli.app, ["--papers-dir", str(papers), "open", "ransford2011mementos"]
    )

    assert result.exit_code == 0
    assert [t.split(":")[0] for t in launched] == ["obsidian"]
    assert "https://example.org/m.pdf" in result.output


def test_every_candidate_below_the_floor_names_the_two_thresholds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: None
) -> None:
    """Reporting "0 new" would read as if the graph were empty."""
    papers = tmp_path / "papers"
    write_note(papers, "Seed", key="a2010seed", doi="10.1/a")
    _fake_graph(monkeypatch, {"W2": _seeded("W2", seeds=("W1",))}, {"W2": _work("W2")})

    result = runner.invoke(cli.app, ["--papers-dir", str(papers), "expand"])

    assert result.exit_code == 0
    assert "1 below the floor (<2 seeds and <50 citations)" in result.stdout
    assert "--min-seeds 1 --min-citations 0" in result.stdout
    assert screening.load(papers).rows == ()


def test_the_reading_list_stays_out_of_every_papers_glob(tmp_path: Path) -> None:
    """A sibling, not a note: one forgotten guard corrupts a bib or a PRISMA count."""
    papers = tmp_path / "papers"
    write_note(papers, "Mementos", key="ransford2011mementos", doi="10.1/a")
    screening.append(papers, [_pending(openalex_id="W2", title="Pending")])
    runner.invoke(cli.app, ["--papers-dir", str(papers), "reading-list"])
    assert (tmp_path / "Reading List.md").is_file()

    out = tmp_path / "refs.bib"
    bib = runner.invoke(
        cli.app, ["--papers-dir", str(papers), "bib", "--out", str(out)]
    )
    found = runner.invoke(
        cli.app, ["--papers-dir", str(papers), "find", "--limit", "9"]
    )
    report = runner.invoke(cli.app, ["--papers-dir", str(papers), "expand", "--report"])

    assert out.read_text(encoding="utf-8").count("@") == 1
    assert "1 notes" in found.stdout
    assert bib.exit_code == 0
    assert "Included       1   notes in the vault" in report.stdout
