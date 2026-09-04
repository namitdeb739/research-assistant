"""The command layer: tag arithmetic, key allocation, and what `near` counts.

`expand`, `relink` and `tidy` had no tests at all, which is why the defects
pinned here survived. No network: the pure helpers are called directly and the
read-only commands run against a temp vault through Typer's runner.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from notes import write_note
from typer.testing import CliRunner

from research_assistant import cli, vault

if TYPE_CHECKING:
    from pathlib import Path

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
