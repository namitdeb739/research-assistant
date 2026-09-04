"""Citation-graph traversal, topic extraction, and the OpenAlex-only builder.

No network: every OpenAlex payload here is a hand-built dict, and the one
function that would make a request is exercised through a fake client.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

from research_assistant import graph, http, sources

if TYPE_CHECKING:
    import httpx


class FakeResponse:
    """Just enough of ``httpx.Response`` for the graph layer."""

    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeClient:
    """Records the params of every GET and replays queued payloads."""

    def __init__(
        self, payloads: list[dict[str, Any]], statuses: list[int] | None = None
    ) -> None:
        self.payloads = payloads
        self.statuses = statuses or []
        self.calls: list[dict[str, str]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(dict(kwargs.get("params") or {}))
        return FakeResponse(
            self.payloads.pop(0) if self.payloads else {"results": [], "meta": {}},
            self.statuses.pop(0) if self.statuses else 200,
        )


def work(ident: str, **fields: Any) -> dict[str, Any]:
    return {"id": f"https://openalex.org/{ident}", **fields}


def test_bare_id_strips_the_url_prefix() -> None:
    assert graph.bare_id("https://openalex.org/W123") == "W123"
    assert graph.bare_id("W123") == "W123"


def test_slug_hyphenates_and_trims() -> None:
    assert graph.slug("Environmental Engineering") == "environmental-engineering"
    assert graph.slug("  Computer Networks & Communications  ") == (
        "computer-networks-communications"
    )


@pytest.mark.parametrize(
    ("count", "expected_batches"),
    [(0, 0), (1, 1), (49, 1), (50, 1), (51, 2), (100, 2), (128, 3)],
)
def test_fetch_works_batches_at_fifty(count: int, expected_batches: int) -> None:
    """OpenAlex accepts 50 ids per filter, so 128 candidates is three calls."""
    ids = [f"W{n}" for n in range(count)]
    client = FakeClient([{"results": []} for _ in range(expected_batches)])

    graph.fetch_works(ids, client=client)  # type: ignore[arg-type]

    assert len(client.calls) == expected_batches
    for call in client.calls:
        assert len(call["filter"].removeprefix("openalex_id:").split("|")) <= 50


def test_fetch_works_skips_merged_or_deleted_ids() -> None:
    """Ids OpenAlex no longer serves come back short, not as an error."""
    client = FakeClient([{"results": [work("W1"), work("W2")]}])

    works = graph.fetch_works(["W1", "W2", "W3"], client=client)  # type: ignore[arg-type]

    assert [graph.bare_id(str(w["id"])) for w in works] == ["W1", "W2"]


def test_citations_of_follows_the_cursor() -> None:
    client = FakeClient(
        [
            {"results": [work("W10")], "meta": {"next_cursor": "page2"}},
            {"results": [work("W11")], "meta": {"next_cursor": None}},
        ]
    )

    works = graph.citations_of(["W1"], client=client)  # type: ignore[arg-type]

    assert [graph.bare_id(str(w["id"])) for w in works] == ["W10", "W11"]
    assert client.calls[0]["cursor"] == "*"
    assert client.calls[1]["cursor"] == "page2"


def test_citations_of_stops_on_an_empty_page() -> None:
    """A cursor that keeps being offered must not loop forever."""
    client = FakeClient([{"results": [], "meta": {"next_cursor": "again"}}])

    assert graph.citations_of(["W1"], client=client) == []  # type: ignore[arg-type]
    assert len(client.calls) == 1


def test_harvest_collects_references_and_related() -> None:
    seed = work("W1", referenced_works=["W2", "W3"], related_works=["W4"])
    client = FakeClient([])

    found = graph.harvest([seed], client=client, forward=False)  # type: ignore[arg-type]

    assert set(found) == {"W2", "W3", "W4"}
    assert found["W2"].provenance == frozenset({graph.REFERENCE})
    assert found["W4"].provenance == frozenset({graph.RELATED})
    assert found["W2"].seeds == frozenset({"W1"})


def test_harvest_merges_provenance_when_found_twice() -> None:
    """A work both cited by one seed and related to another keeps both routes."""
    seeds = [
        work("W1", referenced_works=["W9"]),
        work("W2", related_works=["W9"]),
    ]
    client = FakeClient([])

    found = graph.harvest(seeds, client=client, forward=False)  # type: ignore[arg-type]

    assert found["W9"].provenance == frozenset({graph.REFERENCE, graph.RELATED})
    assert found["W9"].seeds == frozenset({"W1", "W2"})


def test_harvest_never_returns_a_seed() -> None:
    """The seeds cite each other; neither should come back as a candidate."""
    seeds = [
        work("W1", referenced_works=["W2", "W3"]),
        work("W2", referenced_works=["W1"]),
    ]
    client = FakeClient([])

    found = graph.harvest(seeds, client=client, forward=False)  # type: ignore[arg-type]

    assert set(found) == {"W3"}


def test_harvest_forward_records_the_citing_paper() -> None:
    seed = work("W1")
    client = FakeClient(
        [
            {
                "results": [
                    work(
                        "W20",
                        doi="https://doi.org/10.1/X",
                        referenced_works=["W1", "W99"],
                    )
                ],
                "meta": {"next_cursor": None},
            }
        ]
    )

    found = graph.harvest([seed], client=client, backward=False, related=False)  # type: ignore[arg-type]

    assert set(found) == {"W20"}
    assert found["W20"].provenance == frozenset({graph.CITATION})
    assert found["W20"].doi == "10.1/x"
    assert found["W20"].seeds == frozenset({"W1"})


def test_harvest_respects_the_direction_flags() -> None:
    seed = work("W1", referenced_works=["W2"], related_works=["W3"])
    client = FakeClient([])

    found = graph.harvest(
        [seed],
        client=client,  # type: ignore[arg-type]
        backward=False,
        forward=False,
        related=True,
    )

    assert set(found) == {"W3"}


def test_topics_of_returns_names_and_deduped_subfields() -> None:
    payload = {
        "topics": [
            {
                "display_name": "Energy Harvesting in Wireless Networks",
                "subfield": {"display_name": "Electrical and Electronic Engineering"},
            },
            {
                "display_name": "Microbial Fuel Cells and Bioremediation",
                "subfield": {"display_name": "Environmental Engineering"},
            },
            {
                "display_name": "Soil Moisture and Remote Sensing",
                "subfield": {"display_name": "Environmental Engineering"},
            },
        ]
    }

    names, subfields = graph.topics_of(payload)

    assert names == (
        "Energy Harvesting in Wireless Networks",
        "Microbial Fuel Cells and Bioremediation",
        "Soil Moisture and Remote Sensing",
    )
    assert subfields == (
        "electrical-and-electronic-engineering",
        "environmental-engineering",
    )


def test_topics_of_tolerates_a_work_with_none() -> None:
    assert graph.topics_of({}) == ((), ())
    assert graph.topics_of({"topics": [{"display_name": "X"}]}) == (("X",), ())


@pytest.mark.parametrize(
    ("openalex_type", "entry_type"),
    [
        ("article", "article"),
        ("conference-paper", "inproceedings"),
        ("book-chapter", "incollection"),
        ("book", "book"),
        ("report", "techreport"),
        ("dataset", "misc"),
        ("other", "misc"),
        ("something-new", "article"),
    ],
)
def test_paper_from_openalex_maps_entry_types(
    openalex_type: str, entry_type: str
) -> None:
    paper = sources.paper_from_openalex({"title": "T", "type": openalex_type})
    assert paper.entry_type == entry_type


def test_paper_from_openalex_builds_a_full_record() -> None:
    paper = sources.paper_from_openalex(
        {
            "title": "Soil-Powered  Computing",
            "type": "article",
            "doi": "https://doi.org/10.1145/3631410",
            "publication_year": 2023,
            "cited_by_count": 12,
            "authorships": [
                {"author": {"display_name": "Bill Yen"}},
                {"author": {"display_name": "Josiah Hester"}},
            ],
            "primary_location": {"source": {"display_name": "IMWUT"}},
            "open_access": {"oa_status": "gold"},
            "best_oa_location": {"pdf_url": "https://example.org/a.pdf"},
        }
    )

    assert paper.title == "Soil-Powered Computing"
    assert paper.authors == ("Bill Yen", "Josiah Hester")
    assert paper.year == 2023
    assert paper.doi == "10.1145/3631410"
    assert paper.venue == "IMWUT"
    assert paper.citations == 12
    assert paper.open_access == "Gold"
    assert paper.pdf_url == "https://example.org/a.pdf"
    assert paper.url == "https://doi.org/10.1145/3631410"
    assert paper.cite_key() == "yen2023soilpowered"


def test_paper_from_openalex_without_doi_or_venue() -> None:
    paper = sources.paper_from_openalex({"title": "Untitled Work", "type": "article"})

    assert paper.doi is None
    assert paper.url is None
    assert paper.venue is None
    assert paper.authors == ()


def test_retry_after_prefers_the_header() -> None:
    class Headers:
        @staticmethod
        def get(_name: str) -> str:
            return "7"

    response = cast("httpx.Response", type("R", (), {"headers": Headers()})())
    assert http.retry_after(response, 0) == 7.0


def test_retry_after_falls_back_to_exponential_backoff() -> None:
    response = cast("httpx.Response", type("R", (), {"headers": {}})())
    assert http.retry_after(response, 0) == http.BACKOFF_SECONDS
    assert http.retry_after(response, 2) == http.BACKOFF_SECONDS * 4


def test_a_batched_openalex_call_retries_a_transient_error() -> None:
    """The graph layer carries the bulk of the traffic, so it retries too."""
    client = FakeClient([{"results": []}, {"results": []}], statuses=[503, 200])

    graph._get({"filter": "openalex_id:W1"}, client=client, sleep=lambda _: None)  # type: ignore[arg-type]

    assert len(client.calls) == 2
