"""Tests for the pure parts of reference management.

The Crossref/OpenAlex network calls are not exercised here; the parsing,
key generation and rendering they feed into are.
"""

from __future__ import annotations

import pytest

from earth_computers.refs import bibtex, sources
from earth_computers.refs.models import Paper

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


def test_escape_protects_latex_specials() -> None:
    assert bibtex.escape("50% & rising_fast") == r"50\% \& rising\_fast"


def test_urls_are_not_escaped() -> None:
    url = "https://example.com/a_b?x=1&y=2"
    assert f"url   = {{{url}}}" in bibtex.render_entry(Paper(title="T", url=url), "k")


def test_render_sorts_by_cite_key_and_keeps_header() -> None:
    out = bibtex.render([(ENTS, "zeta2020a"), (ENTS, "alpha2019b")])
    assert out.startswith("% Generated")
    assert out.index("alpha2019b") < out.index("zeta2020a")


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
