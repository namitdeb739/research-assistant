"""Corpus maintenance: retraction notices, metadata drift, and duplicate pairs.

The Crossref payloads here are the shapes the live API actually returns,
including the one that decides the design: Elsevier registered `update-to` on
the retracted article as well as on the notice, so only `updated-by` can tell
the two apart.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from notes import write_note

from research_assistant import health, search

if TYPE_CHECKING:
    from pathlib import Path


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status_code = 200
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    """Answers the Crossref query route, and records the filters it was asked for."""

    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = items
        self.filters: list[str] = []

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        params = kwargs.get("params") or {}
        self.filters.append(str(params.get("filter", "")))
        wanted = {
            part.removeprefix("doi:")
            for part in str(params.get("filter", "")).split(",")
        }
        return _FakeResponse(
            {
                "message": {
                    "items": [
                        item
                        for item in self._items
                        if str(item.get("DOI", "")).lower() in wanted
                    ]
                }
            }
        )


def records_for(papers: Path, **kwargs: Any) -> list[search.Record]:
    write_note(papers, **kwargs)
    return search.load(papers)


def test_the_query_route_wraps_its_results_in_message_items() -> None:
    """Unlike the single-work route `sources.fetch_crossref` reads."""
    client = _FakeClient([{"DOI": "10.1/a", "title": ["A"]}])

    found = health.fetch_records(["10.1/A"], client=client)  # type: ignore[arg-type]

    assert set(found) == {"10.1/a"}


def test_dois_are_batched_forty_at_a_time() -> None:
    """40, not 50, to keep the encoded filter URL under about 2 kB."""
    client = _FakeClient([])

    health.fetch_records([f"10.1/{n}" for n in range(85)], client=client)  # type: ignore[arg-type]

    assert len(client.filters) == 3
    assert all(f.count("doi:") <= 40 for f in client.filters)


def test_a_venue_differing_only_by_an_html_entity_is_not_drift(tmp_path: Path) -> None:
    """`tidy` already unescapes, so a tidied note must report nothing."""
    papers = tmp_path / "papers"
    record = records_for(
        papers,
        name="A",
        key="a2020one",
        venue="Environmental Science & Technology",
        doi="10.1/a",
    )[0]

    drift = health.drift_of(
        record,
        {"container-title": ["Environmental Science &amp; Technology"]},
    )

    assert drift == ()


def test_a_field_crossref_does_not_know_is_not_drift(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    record = records_for(papers, name="A", key="a2020one", doi="10.1/a")[0]

    assert health.drift_of(record, {"container-title": []}) == ()


def test_a_retitled_paper_is_drift(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    record = records_for(
        papers, name="Old title", key="a2020one", doi="10.1/a", venue="SenSys"
    )[0]

    drift = health.drift_of(record, {"title": ["A completely different title"]})

    assert [d.field for d in drift] == ["title"]
    assert drift[0].upstream == "A completely different title"


def test_a_version_of_record_that_gained_a_subtitle_still_pairs(
    tmp_path: Path,
) -> None:
    """The case plain Jaccard fails: it scores this 0.5 and drops the pair."""
    papers = tmp_path / "papers"
    write_note(
        papers,
        "Soil powered sensing networks",
        key="yen2022soil",
        year=2022,
        doi="10.48550/arXiv.2201.1",
    )
    write_note(
        papers,
        "Soil powered sensing networks An Empirical Study of Deployment",
        key="yen2023soil",
        year=2023,
        doi="10.1145/3596262",
    )

    pairs = health.find_duplicates(search.load(papers))

    assert len(pairs) == 1
    assert pairs[0].preprint.cite_key == "yen2022soil"
    assert pairs[0].containment == 1.0
    assert pairs[0].jaccard < 1.0


def test_a_two_word_title_is_too_short_to_compare(tmp_path: Path) -> None:
    """Containment is trivially 1.0 when the shorter title is a couple of tokens."""
    papers = tmp_path / "papers"
    write_note(papers, "Mementos", key="a2020one", year=2020)
    write_note(papers, "Mementos and other stories of power", key="a2021two", year=2021)

    assert health.find_duplicates(search.load(papers)) == ()


def test_stopwords_do_not_inflate_the_similarity_of_unrelated_titles(
    tmp_path: Path,
) -> None:
    papers = tmp_path / "papers"
    write_note(papers, "A study of the effects of the wind", key="a2020one", year=2020)
    write_note(papers, "A survey of the results of the tide", key="a2021two", year=2021)

    assert health.find_duplicates(search.load(papers)) == ()


def test_two_papers_by_one_author_in_one_year_are_not_a_pair(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    write_note(papers, "Backscatter communication for tags", key="a2020one", year=2020)
    write_note(
        papers, "Energy harvesting from soil microbes", key="a2020two", year=2020
    )

    assert health.find_duplicates(search.load(papers)) == ()


def test_a_pair_more_than_two_years_apart_is_not_blocked_together(
    tmp_path: Path,
) -> None:
    papers = tmp_path / "papers"
    write_note(
        papers,
        "Early",
        title="Soil powered sensing networks",
        key="a2015one",
        year=2015,
    )
    write_note(
        papers, "Late", title="Soil powered sensing networks", key="a2023two", year=2023
    )

    assert health.find_duplicates(search.load(papers)) == ()


def test_the_pair_is_ordered_preprint_first(tmp_path: Path) -> None:
    """So the printed recommendation names the right note to delete."""
    papers = tmp_path / "papers"
    write_note(
        papers,
        "Soil powered sensing networks (published)",
        title="Soil powered sensing networks",
        key="yen2023vor",
        year=2023,
        doi="10.1145/3596262",
    )
    write_note(
        papers,
        "Soil powered sensing networks (preprint)",
        title="Soil powered sensing networks",
        key="yen2022pre",
        year=2022,
        doi="10.48550/arXiv.2201.1",
    )

    pairs = health.find_duplicates(search.load(papers))

    assert pairs[0].preprint.cite_key == "yen2022pre"
    assert pairs[0].version_of_record.cite_key == "yen2023vor"


LANCET_DOI = "10.1016/s0140-6736(20)31180-6"
LANCET = {
    "DOI": LANCET_DOI,
    "title": ["Hydroxychloroquine or chloroquine with or without a macrolide"],
    # Elsevier registered this on the article as well as on the notice.
    "update-to": [{"DOI": "10.1016/s0140-6736(20)31324-6", "type": "retraction"}],
    "updated-by": [
        {
            "DOI": "10.1016/s0140-6736(20)31324-6",
            "type": "retraction",
            "source": "retraction-watch",
            "updated": {"date-time": "2020-06-05T00:00:00Z"},
        }
    ],
}


def test_check_reports_a_retracted_paper(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    write_note(
        papers,
        "Hydroxychloroquine or chloroquine with or without a macrolide",
        key="mehra2020hydroxychloroquine",
        doi=LANCET_DOI,
    )
    client = _FakeClient([LANCET])

    report = health.check(
        search.load(papers),
        client=client,  # type: ignore[arg-type]
        drift=False,
        duplicates=False,
    )

    assert not report.clean
    assert [kind for _, kind in report.retracted] == ["retraction"]


def test_a_note_that_is_itself_the_notice_is_not_reported(tmp_path: Path) -> None:
    """`relation.retraction` is the notice's own declaration, not a title guess."""
    papers = tmp_path / "papers"
    write_note(papers, "Retraction notice", key="lancet2020retraction", doi="10.1/n")
    notice = {
        "DOI": "10.1/n",
        "updated-by": [{"DOI": LANCET_DOI, "type": "retraction"}],
        "relation": {"retraction": [{"id": LANCET_DOI}]},
    }
    client = _FakeClient([notice])

    report = health.check(
        search.load(papers),
        client=client,  # type: ignore[arg-type]
        drift=False,
        duplicates=False,
    )

    assert report.retracted == ()


def test_a_note_with_no_doi_is_reported_as_unchecked(tmp_path: Path) -> None:
    """Crossref cannot know about a USENIX paper that mints none."""
    papers = tmp_path / "papers"
    write_note(papers, "Passive Wi-Fi", key="kellogg2016passive")
    client = _FakeClient([])

    report = health.check(
        search.load(papers),
        client=client,  # type: ignore[arg-type]
        drift=False,
        duplicates=False,
    )

    assert report.unchecked == ("kellogg2016passive",)
    assert report.checked == 0


def test_title_similarity_of_an_empty_title_is_zero() -> None:
    assert health.title_similarity("", "anything at all here") == (0.0, 0.0)
