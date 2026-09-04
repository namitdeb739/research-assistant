"""Tests for the pure parts of reference management.

The Crossref/OpenAlex network calls are not exercised here; the parsing,
key generation and rendering they feed into are.
"""

from __future__ import annotations

import pytest

from research_assistant import bibtex, sources
from research_assistant.models import Paper

ENTS = Paper(
    title="ENTS: Experiences in Co-Designed Environmental Sensing",
    authors=("John Madden", "Colleen Josephson"),
    year=2026,
    doi="10.1145/3774906.3802780",
    venue="SenSys",
    entry_type="inproceedings",
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.1145/3631410", "10.1145/3631410"),
        ("https://doi.org/10.1145/3631410", "10.1145/3631410"),
        ("https://dx.doi.org/10.1145/3631410", "10.1145/3631410"),
        ("doi:10.1145/3631410", "10.1145/3631410"),
        ("  10.1145/3631410  ", "10.1145/3631410"),
        ("10.1145/ABC", "10.1145/abc"),
    ],
)
def test_normalize_doi(raw: str, expected: str) -> None:
    assert sources.normalize_doi(raw) == expected


def test_cite_key_skips_stopwords() -> None:
    paper = Paper(title="On the Design of Things", authors=("Ada Lovelace",), year=2020)
    assert paper.cite_key() == "lovelace2020design"


def test_cite_key_handles_missing_metadata() -> None:
    assert Paper(title="Untitled").cite_key() == "anonndUntitled".lower()


def test_cite_key_strips_punctuation() -> None:
    assert ENTS.cite_key() == "madden2026ents"


def test_first_author_surname() -> None:
    assert ENTS.first_author_surname == "Madden"
    assert Paper(title="x").first_author_surname is None


def test_render_entry_uses_booktitle_for_proceedings() -> None:
    entry = bibtex.render_entry(ENTS, "madden2026ents")
    assert entry.startswith("@inproceedings{madden2026ents,")
    assert "booktitle = {SenSys}" in entry
    assert "author    = {John Madden and Colleen Josephson}" in entry


def test_render_entry_uses_journal_for_articles() -> None:
    paper = Paper(title="T", venue="IMWUT", entry_type="article")
    assert "journal = {IMWUT}" in bibtex.render_entry(paper, "k")


def test_render_entry_uses_howpublished_for_misc() -> None:
    paper = Paper(title="T", venue="FYP final presentation", entry_type="misc")
    assert "howpublished = {FYP final presentation}" in bibtex.render_entry(paper, "k")


def test_escape_protects_latex_specials() -> None:
    assert bibtex.escape("50% & rising_fast") == r"50\% \& rising\_fast"


def test_urls_are_not_escaped() -> None:
    url = "https://example.com/a_b?x=1&y=2"
    assert f"url   = {{{url}}}" in bibtex.render_entry(Paper(title="T", url=url), "k")


def test_render_sorts_by_cite_key_and_keeps_header() -> None:
    out = bibtex.render([(ENTS, "zeta2020a"), (ENTS, "alpha2019b")])
    assert out.startswith("% Generated")
    assert out.index("alpha2019b") < out.index("zeta2020a")


# The shape Crossref actually returns for the retracted Lancet/Surgisphere
# paper: `updated-by` carries the back-pointer, and Elsevier registered
# `update-to` on the article as well, which is why that field is not used.
LANCET = {
    "title": ["Hydroxychloroquine or chloroquine with or without a macrolide"],
    "DOI": "10.1016/s0140-6736(20)31180-6",
    "update-to": [{"DOI": "10.1016/s0140-6736(20)31324-6", "type": "retraction"}],
    "updated-by": [
        {
            "DOI": "10.1016/s0140-6736(20)31324-6",
            "type": "retraction",
            "source": "retraction-watch",
            "updated": {"date-time": "2020-06-05T00:00:00Z"},
        },
        {
            "DOI": "10.1016/s0140-6736(20)31290-3",
            "type": "expression_of_concern",
            "source": "publisher",
            "updated": {"date-time": "2020-06-02T00:00:00Z"},
        },
    ],
}


def test_the_strongest_notice_wins_over_an_expression_of_concern() -> None:
    assert sources.strongest(sources.notices(LANCET)) == "retraction"


def test_update_to_on_the_retracted_article_is_ignored() -> None:
    """Elsevier registers it on both sides, so it cannot tell them apart."""
    only_update_to = {"update-to": LANCET["update-to"]}

    assert sources.notices(only_update_to) == ()
    assert sources.strongest(sources.notices(only_update_to)) is None


def test_an_erratum_is_reported_but_is_not_a_retraction() -> None:
    message = {"updated-by": [{"DOI": "10.1/e", "type": "correction"}]}

    found = sources.notices(message)

    assert len(found) == 1
    assert sources.strongest(found) is None


def test_a_record_declaring_relation_retraction_is_the_notice_not_the_article() -> None:
    """The notice's own declaration of what it retracts, not a title guess."""
    notice = {"relation": {"retraction": [{"id": "10.1016/s0140-6736(20)31180-6"}]}}

    assert sources.is_notice(notice)
    assert not sources.is_notice(LANCET)


def test_build_paper_records_a_retraction_without_a_second_request() -> None:
    """`fetch_crossref` already returns the whole message."""
    assert sources.build_paper(LANCET).retracted == "retraction"


def test_build_paper_reads_the_fields_a_bibliography_needs() -> None:
    message = {
        "title": ["A paper"],
        "DOI": "10.1/a",
        "volume": "7",
        "issue": "3",
        "page": "1-28",
        "publisher": "ACM",
        "editor": [{"given": "Ada", "family": "Lovelace"}],
        "published-print": {"date-parts": [[2023, 9, 14]]},
    }

    paper = sources.build_paper(message)

    assert (paper.volume, paper.number, paper.pages) == ("7", "3", "1-28")
    assert paper.publisher == "ACM"
    assert paper.editors == ("Ada Lovelace",)
    assert (paper.year, paper.month) == (2023, "9")


def test_a_work_with_only_a_year_has_no_month() -> None:
    paper = sources.build_paper(
        {"title": ["A paper"], "issued": {"date-parts": [[2023]]}}
    )

    assert paper.year == 2023
    assert paper.month is None


def test_paper_from_openalex_joins_the_page_range() -> None:
    """OpenAlex keeps it split, and is the only source for a work Crossref lacks."""
    paper = sources.paper_from_openalex(
        {
            "title": "Passive Wi-Fi",
            "biblio": {
                "volume": "2",
                "issue": "1",
                "first_page": "5",
                "last_page": "9",
            },
        }
    )

    assert paper.pages == "5--9"
    assert (paper.volume, paper.number) == ("2", "1")


def test_an_article_renders_volume_issue_and_pages() -> None:
    """Every @article came out without them, which no reader forgives."""
    paper = Paper(
        title="Soil-Powered Computing",
        authors=("Bill Yen",),
        year=2023,
        venue="IMWUT",
        volume="7",
        number="3",
        pages="1--28",
        entry_type="article",
    )

    out = bibtex.render_entry(paper, "yen2023soil")

    assert "volume  = {7}" in out
    assert "number  = {3}" in out
    assert "pages   = {1--28}" in out


def test_a_conference_paper_gets_no_volume_or_issue() -> None:
    """Emitting every field everywhere is how a talk ends up with an issue number."""
    paper = Paper(
        title="Mementos",
        authors=("Benjamin Ransford",),
        year=2011,
        venue="ASPLOS",
        volume="7",
        number="3",
        pages="159--170",
        publisher="ACM",
        entry_type="inproceedings",
    )

    out = bibtex.render_entry(paper, "ransford2011mementos")

    assert "volume" not in out
    assert "number" not in out
    assert "pages" in out
    assert "publisher" in out


def test_editors_render_as_one_and_joined_field() -> None:
    paper = Paper(
        title="A chapter",
        editors=("Ada Lovelace", "Alan Turing"),
        publisher="MIT Press",
        entry_type="incollection",
    )

    out = bibtex.render_entry(paper, "anon2020chapter")

    assert "editor" in out
    assert "Ada Lovelace and Alan Turing" in out


def test_a_retracted_paper_renders_no_extra_bibtex_field() -> None:
    """`retracted` is a fact about the resource, not something biblatex knows."""
    paper = Paper(title="Withdrawn", year=2020, retracted="retraction")

    assert "retract" not in bibtex.render_entry(paper, "anon2020withdrawn")


def test_render_refuses_to_write_a_bibliography_with_a_repeated_key() -> None:
    """BibTeX keeps one entry of two, so a citation would point at the wrong paper."""
    other = Paper(title="Something else entirely", authors=("John Madden",), year=2026)

    with pytest.raises(bibtex.BibtexError) as caught:
        bibtex.render([(ENTS, "madden2026ents"), (other, "madden2026ents")])

    assert "madden2026ents" in str(caught.value)
    assert "Something else entirely" in str(caught.value)


def test_duplicate_keys_reports_only_the_repeated_ones() -> None:
    repeated = bibtex.duplicate_keys(
        [(ENTS, "alpha2019b"), (ENTS, "zeta2020a"), (ENTS, "alpha2019b")]
    )

    assert list(repeated) == ["alpha2019b"]
    assert len(repeated["alpha2019b"]) == 2


def test_strip_jats_flattens_markup() -> None:
    raw = "<jats:p>Soil <jats:italic>microbial</jats:italic>  cells.</jats:p>"
    assert sources._strip_jats(raw) == "Soil microbial cells."


def test_openalex_abstract_reconstructs_word_order() -> None:
    work = {"abstract_inverted_index": {"Batteryless": [0], "sensing": [1], "is": [2]}}
    assert sources._openalex_abstract(work) == "Batteryless sensing is"


def test_openalex_abstract_absent() -> None:
    assert sources._openalex_abstract({}) is None


def test_build_paper_merges_openalex_enrichment() -> None:
    crossref = {
        "title": ["Soil-Powered Computing"],
        "author": [{"given": "Bill", "family": "Yen"}],
        "issued": {"date-parts": [[2023]]},
        "DOI": "10.1145/3631410",
        "container-title": ["IMWUT"],
        "type": "journal-article",
    }
    openalex = {"cited_by_count": 42, "open_access": {"oa_status": "gold"}}
    paper = sources.build_paper(crossref, openalex)

    assert paper.title == "Soil-Powered Computing"
    assert paper.authors == ("Bill Yen",)
    assert paper.year == 2023
    assert paper.entry_type == "article"
    assert paper.citations == 42
    assert paper.open_access == "Gold"


def test_build_paper_without_openalex() -> None:
    paper = sources.build_paper({"title": ["T"], "type": "proceedings-article"})
    assert paper.entry_type == "inproceedings"
    assert paper.citations is None
    assert paper.open_access is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("W2300484078", "W2300484078"),
        ("w2300484078", "W2300484078"),
        ("openalex:W2300484078", "W2300484078"),
        ("https://openalex.org/W2300484078", "W2300484078"),
        ("https://api.openalex.org/works/W2300484078", "W2300484078"),
        ("  W2300484078  ", "W2300484078"),
    ],
)
def test_as_openalex_id_accepts_every_form_it_is_copied_in(
    raw: str, expected: str
) -> None:
    assert sources.as_openalex_id(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["10.1145/3631410", "https://doi.org/10.1145/3631410", "W", "A2300484078", ""],
)
def test_as_openalex_id_rejects_anything_else(raw: str) -> None:
    assert sources.as_openalex_id(raw) is None


def test_paper_from_openalex_falls_back_to_the_landing_page_without_a_doi() -> None:
    """USENIX mints no DOI, so the landing page is the only locator there is."""
    work = {
        "title": "Passive Wi-Fi: Bringing Low Power to Wi-Fi Transmissions",
        "publication_year": 2016,
        "type": "conference-paper",
        "primary_location": {
            "landing_page_url": "https://www.usenix.org/conference/nsdi16/…/kellogg"
        },
    }
    paper = sources.paper_from_openalex(work)

    assert paper.doi is None
    assert paper.url == "https://www.usenix.org/conference/nsdi16/…/kellogg"
    assert paper.entry_type == "inproceedings"


def test_paper_from_openalex_prefers_the_doi_url_when_there_is_one() -> None:
    work = {
        "title": "T",
        "doi": "https://doi.org/10.1145/3631410",
        "primary_location": {"landing_page_url": "https://example.invalid/t"},
    }
    assert sources.paper_from_openalex(work).url == "https://doi.org/10.1145/3631410"


class _StubResponse:
    """Just enough of ``httpx.Response`` for the source lookups."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.status_code = 200
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class _StubClient:
    """Answers the Crossref lookup and the OpenAlex enrichment by URL."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url: str, **_kwargs: object) -> _StubResponse:
        self.urls.append(url)
        if "crossref" in url:
            return _StubResponse(
                {"message": {"title": ["Mementos"], "DOI": "10.1145/1993316"}}
            )
        return _StubResponse({"id": "https://openalex.org/W2300484078"})


def test_resolving_a_doi_keeps_the_openalex_id_it_already_fetched() -> None:
    """Discarding it wrote `openalex_id: null` and made `relink` re-query."""
    client = _StubClient()

    paper, openalex_id = sources.resolve_source("10.1145/1993316", client=client)  # type: ignore[arg-type]

    assert paper.title == "Mementos"
    assert openalex_id == "W2300484078"


def test_manual_source_still_gets_a_cite_key() -> None:
    """A talk with no DOI is cited the same way anything else is."""
    paper = Paper(
        title="Earth Computers",
        authors=("Sean Wang",),
        year=2026,
        venue="NUS School of Computing, FYP final presentation",
        entry_type="misc",
    )
    assert paper.cite_key() == "wang2026earth"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "Environmental Science &amp; Technology",
            "Environmental Science & Technology",
        ),
        (
            "Energy Harvesting &amp;amp; Energy-Neutral Sensing",
            "Energy Harvesting & Energy-Neutral Sensing",
        ),
        ("nothing to resolve", "nothing to resolve"),
    ],
)
def test_unescape_resolves_entities_to_a_fixed_point(raw: str, expected: str) -> None:
    assert sources.unescape(raw) == expected


def test_strip_jats_keeps_comparisons_that_look_like_tags() -> None:
    """``p &lt; 0.05] ... [ p &gt;`` is an inequality, not an element."""
    raw = (
        "<jats:p>effect for sensor type [ p &lt; 0.05] "
        "but timestamp [ p &gt; 0.1]</jats:p>"
    )
    assert sources._strip_jats(raw) == (
        "effect for sensor type [ p < 0.05] but timestamp [ p > 0.1]"
    )


def test_build_paper_resolves_entities_in_title_and_venue() -> None:
    paper = sources.build_paper(
        {
            "title": ["Connecting the Twins: Digital Twin Technology &amp; Networks"],
            "container-title": ["Environmental Science &amp; Technology"],
            "type": "journal-article",
        }
    )
    assert paper.title == "Connecting the Twins: Digital Twin Technology & Networks"
    assert paper.venue == "Environmental Science & Technology"


def test_paper_from_openalex_resolves_entities_too() -> None:
    work = {
        "title": "Soil &amp; Sensing",
        "primary_location": {"source": {"display_name": "Energy &amp; Environment"}},
    }
    paper = sources.paper_from_openalex(work)
    assert paper.title == "Soil & Sensing"
    assert paper.venue == "Energy & Environment"
