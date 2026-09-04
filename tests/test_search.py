"""Ranked search over the vault, and the note↔PDF join."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from notes import write_note

from research_assistant import search

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    """A small vault standing in for the real one, same shapes throughout."""
    papers = tmp_path / "papers"
    write_note(
        papers,
        "Mementos",
        key="ransford2011mementos",
        year=2011,
        citations=350,
        doi="10.1145/1950365.1950386",
        abstract=(
            "Transiently powered computing devices rely on programs that complete "
            "a task before energy starvation. Mementos protects them with "
            "automatic energy-aware state checkpointing to nonvolatile memory."
        ),
        topics=("Energy Harvesting in Wireless Networks",),
        tags=("paper", "seed", "topic/hardware-and-architecture"),
        pdf="ransford2011mementos.pdf",
    )
    write_note(
        papers,
        "Checkpointing considered harmful",
        key="lovelace2019checkpointing",
        year=2019,
        citations=0,
        abstract="A study of soil moisture in the field.",
        topics=("Energy Harvesting in Wireless Networks",),
    )
    write_note(
        papers,
        "ALFRED",
        key="maioli2021alfred",
        year=2021,
        citations=None,
        abstract=(
            "A virtual memory abstraction resolving the dichotomy between "
            "volatile and non-volatile memory in intermittent computing."
        ),
        cites=("Mementos",),
        topics=("Green IT and Sustainability",),
        pdf_url="https://example.invalid/alfred.pdf",
    )
    (tmp_path / "pdfs").mkdir()
    (tmp_path / "pdfs" / "ransford2011mementos.pdf").write_bytes(b"%PDF-1.4\n")
    return papers


def test_tokenize_drops_stopwords_and_single_characters() -> None:
    tokens = search.tokenize("The Future of Sensing is Batteryless, a X-ray!")

    assert tokens == ["future", "sensing", "batteryless", "ray"]


def test_tokenize_survives_unicode_and_markup() -> None:
    # Real titles here carry HTML entities and accented author names. Accented
    # letters fall outside the ASCII word pattern, so "Pérez" splits. Searching
    # "penichet" still reaches the paper, which is what actually matters.
    assert search.tokenize("Pérez-Penichet: <i>in vitro</i> study") == [
        "rez",
        "penichet",
        "vitro",
        "study",
    ]


def test_a_title_match_outranks_an_abstract_match(vault_dir: Path) -> None:
    records = search.load(vault_dir)

    hits = search.rank(records, "checkpointing")

    # Both notes contain the term once; only one has it in its title.
    assert [hit.record.cite_key for hit in hits] == [
        "lovelace2019checkpointing",
        "ransford2011mementos",
    ]


def test_a_ubiquitous_term_counts_for_less_than_a_rare_one(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    for index in range(4):
        write_note(
            papers,
            f"Paper {index}",
            key=f"author20{index}0paper",
            abstract="sensing" + (" backscatter" if index == 0 else ""),
        )
    records = search.load(papers)

    # Every note says "sensing", so it separates nothing; only one says
    # "backscatter", and that note must clear the rest by a wide margin.
    hits = search.rank(records, "sensing backscatter")
    scores = [hit.score for hit in hits]

    assert hits[0].record.cite_key == "author2000paper"
    assert scores[0] > 2 * scores[1]
    assert len(set(scores[1:])) == 1  # the other three are indistinguishable


def test_no_match_is_no_hit(vault_dir: Path) -> None:
    assert search.rank(search.load(vault_dir), "photovoltaic") == []


def test_an_empty_query_ranks_nothing(vault_dir: Path) -> None:
    # A query of nothing but stopwords must not silently become "everything".
    assert search.rank(search.load(vault_dir), "the of and") == []


def test_snippet_lands_on_the_query_terms(vault_dir: Path) -> None:
    records = search.load(vault_dir)

    (hit,) = [h for h in search.rank(records, "intermittent") if h.field]

    assert hit.field == "abstract"
    assert f"{search.MARK_OPEN}intermittent{search.MARK_CLOSE}" in hit.snippet
    # The window is bounded, and does not run past the section it came from.
    assert len(hit.snippet) <= search.SNIPPET_WIDTH + 16
    assert "## Notes" not in hit.snippet


def test_filters_compose_and_run_before_ranking(vault_dir: Path) -> None:
    records = search.load(vault_dir)

    kept = search.apply_filters(
        records, topic="Energy Harvesting", min_year=2015, min_citations=0
    )

    assert [r.cite_key for r in kept] == ["lovelace2019checkpointing"]


def test_tag_filter_matches_a_nested_subfield_tag(vault_dir: Path) -> None:
    records = search.load(vault_dir)

    assert [r.cite_key for r in search.apply_filters(records, tag="seed")] == [
        "ransford2011mementos"
    ]
    # `topic/hardware-and-architecture` is reachable by its leaf.
    assert [
        r.cite_key
        for r in search.apply_filters(records, tag="hardware-and-architecture")
    ] == ["ransford2011mementos"]


def test_a_missing_citation_count_is_not_a_zero(vault_dir: Path) -> None:
    records = search.load(vault_dir)
    order = [hit.record.cite_key for hit in search.by_citations(records)]

    # 350, then 0, then the unrecorded one: a null sorts last, not with the zeros.
    assert order == [
        "ransford2011mementos",
        "lovelace2019checkpointing",
        "maioli2021alfred",
    ]
    assert (
        next(r for r in records if r.cite_key == "maioli2021alfred").citations is None
    )


def test_resolution_by_key_name_and_doi(vault_dir: Path) -> None:
    records = search.load(vault_dir)

    for target in (
        "ransford2011mementos",
        "Mementos",
        "10.1145/1950365.1950386",
        "https://doi.org/10.1145/1950365.1950386",
    ):
        assert search.resolve(records, target).cite_key == "ransford2011mementos"


def test_an_ambiguous_target_errors_rather_than_guessing(vault_dir: Path) -> None:
    write_note(vault_dir, "Checkpointing in practice", key="turing2022checkpointing")
    records = search.load(vault_dir)

    # A fragment shared by two notes: picking one silently miscites a paper.
    with pytest.raises(search.SearchError, match="matches 2 notes"):
        search.resolve(records, "checkpointing")

    with pytest.raises(search.SearchError, match="matches no note"):
        search.resolve(records, "nothing-like-this")


def test_an_exact_key_beats_a_fuzzy_match_elsewhere(vault_dir: Path) -> None:
    # A note whose *title* contains another note's cite key.
    write_note(
        vault_dir,
        "On ransford2011mementos and its successors",
        key="hopper2024on",
    )

    resolved = search.resolve(search.load(vault_dir), "ransford2011mementos")

    assert resolved.path.stem == "Mementos"


def test_cites_and_cited_by_are_both_reachable(vault_dir: Path) -> None:
    records = search.load(vault_dir)
    alfred = search.resolve(records, "maioli2021alfred")
    mementos = search.resolve(records, "ransford2011mementos")

    cites, unresolved = search.cites_in_vault(records, alfred)

    assert [r.cite_key for r in cites] == ["ransford2011mementos"]
    assert unresolved == ()
    # The reverse direction is derived by scanning; there is no cited_by property.
    assert [r.cite_key for r in search.cited_by(records, mementos)] == [
        "maioli2021alfred"
    ]


def test_unresolved_citations_are_counted_separately(vault_dir: Path) -> None:
    write_note(
        vault_dir,
        "Protean",
        key="bakar2022protean",
        cites=("Mementos", "A paper nobody has added yet"),
    )
    records = search.load(vault_dir)

    cites, unresolved = search.cites_in_vault(
        records, search.resolve(records, "bakar2022protean")
    )

    assert [r.cite_key for r in cites] == ["ransford2011mementos"]
    assert unresolved == ("A paper nobody has added yet",)


def test_a_pdf_on_disk_is_distinguished_from_one_merely_claimed(
    vault_dir: Path,
) -> None:
    records = search.load(vault_dir)
    mementos = search.resolve(records, "ransford2011mementos")
    alfred = search.resolve(records, "maioli2021alfred")

    assert mementos.has_pdf
    assert mementos.pdf_path is not None
    assert mementos.pdf_path.is_file()
    # Claims nothing, but records where to get it, which is not the same as
    # having it, and `pdf` says so on stderr rather than printing a path.
    assert not alfred.has_pdf
    assert alfred.pdf_url == "https://example.invalid/alfred.pdf"


def test_pdf_filter_splits_the_readable_from_the_reading_list(
    vault_dir: Path,
) -> None:
    records = search.load(vault_dir)

    readable = search.apply_filters(records, has_pdf=True)
    wanted = search.apply_filters(records, has_pdf=False)

    assert [r.cite_key for r in readable] == ["ransford2011mementos"]
    assert len(wanted) == 2


def test_a_pdf_resolves_back_to_its_note(vault_dir: Path) -> None:
    records = search.load(vault_dir)

    for target in (
        "ransford2011mementos.pdf",
        "ransford2011mementos",
        "pdfs/ransford2011mementos.pdf",
        "/Users/someone/Obsidian/pdfs/ransford2011mementos.pdf",
    ):
        assert search.resolve_pdf(records, target).path.stem == "Mementos"


def test_a_clean_vault_audits_clean(vault_dir: Path) -> None:
    records = search.load(vault_dir)

    report = search.audit(records, pdfs_dir=search.pdfs_dir_for(vault_dir))

    assert report.clean
    assert (report.total, report.with_pdf) == (3, 1)
    # One note records a pdf_url it has not been used to fetch; one has neither.
    assert (report.without_pdf_with_url, report.without_pdf_no_url) == (1, 1)


def test_the_audit_catches_each_way_the_two_sides_drift(vault_dir: Path) -> None:
    pdfs = search.pdfs_dir_for(vault_dir)
    # 1. a PDF matching no note at all
    (pdfs / "nobody2001orphan.pdf").write_bytes(b"%PDF-1.4\n")
    # 2. a note claiming a PDF that is not there
    write_note(vault_dir, "Shepherd", key="geissdoerfer2019shepherd", pdf="missing.pdf")
    # 3. a PDF on disk whose note has not adopted it
    write_note(vault_dir, "Protean", key="bakar2022protean")
    (pdfs / "bakar2022protean.pdf").write_bytes(b"%PDF-1.4\n")
    # 4. a note whose link disagrees with its own cite key
    write_note(
        vault_dir, "Judo", key="varshney2022judo", pdf="ransford2011mementos.pdf"
    )

    report = search.audit(search.load(vault_dir), pdfs_dir=pdfs)

    assert not report.clean
    assert report.orphans == ("nobody2001orphan",)
    assert report.missing == ("geissdoerfer2019shepherd",)
    assert report.unclaimed == ("bakar2022protean",)
    assert report.mismatched == (("varshney2022judo", "ransford2011mementos.pdf"),)


def test_a_note_without_a_title_is_skipped_not_fatal(vault_dir: Path) -> None:
    # `topics.base` and stray notes live alongside the papers; a note the
    # template never wrote must not take the whole search down.
    (vault_dir / "scratch.md").write_text("just some prose\n", encoding="utf-8")

    assert len(search.load(vault_dir)) == 3


def test_loading_a_missing_folder_says_which_one(tmp_path: Path) -> None:
    with pytest.raises(search.SearchError, match="VAULT_PAPERS_DIR"):
        search.load(tmp_path / "nowhere")


def test_an_author_filter_matches_any_listed_name(vault_dir: Path) -> None:
    records = search.load(vault_dir)

    assert len(search.apply_filters(records, author="turing")) == 3
    assert search.apply_filters(records, author="Nobody") == []


def test_a_quoted_phrase_must_appear_verbatim(vault_dir: Path) -> None:
    """Both words rank the note; the phrase then filters what ranked."""
    write_note(
        vault_dir, "Two words apart", key="a2020apart", abstract="stealing then work"
    )
    write_note(
        vault_dir, "Two words together", key="a2020together", abstract="work stealing"
    )
    records = search.load(vault_dir)

    loose = {hit.record.cite_key for hit in search.rank(records, "work stealing")}
    exact = {hit.record.cite_key for hit in search.rank(records, '"work stealing"')}

    assert "a2020together" in exact
    assert "a2020apart" not in exact
    assert "a2020apart" in loose or not loose


def test_a_phrase_keeps_words_the_stopword_list_eats(vault_dir: Path) -> None:
    """`work` is a stopword, so quoting has to bypass the filter or be useless."""
    parsed = search.parse_query('"work stealing"')

    assert parsed.phrases == ("work stealing",)
    assert "work" in parsed.terms


def test_a_field_scope_restricts_where_a_term_counts(vault_dir: Path) -> None:
    write_note(vault_dir, "Backscatter in the title", key="a2020title")
    write_note(
        vault_dir, "Something else", key="a2020body", abstract="all about backscatter"
    )
    records = search.load(vault_dir)

    scoped = {hit.record.cite_key for hit in search.rank(records, "title:backscatter")}

    assert "a2020title" in scoped
    assert "a2020body" not in scoped


def test_an_unknown_field_prefix_is_treated_as_an_ordinary_term() -> None:
    parsed = search.parse_query("nosuchfield:backscatter")

    assert parsed.scope is None


def test_prefix_expansion_reaches_a_longer_form_of_the_same_word(
    vault_dir: Path,
) -> None:
    """The stemmer objection stands; this imposes only the corpus's own terms."""
    write_note(
        vault_dir, "Tag reading", key="a2020tags", abstract="backscattering tags"
    )
    records = search.load(vault_dir)

    plain = {hit.record.cite_key for hit in search.rank(records, "backscatter")}
    wider = {
        hit.record.cite_key for hit in search.rank(records, "backscatter", expand=True)
    }

    assert "a2020tags" not in plain
    assert "a2020tags" in wider


def test_prefix_expansion_does_not_reach_a_short_word() -> None:
    """`bio` must not reach `bios`: the floor is longer than the word."""
    assert search._expansions("bio", {"bios", "biology"}) == set()


def test_prefix_expansion_never_shortens() -> None:
    assert search._expansions("backscatter", {"backsc"}) == set()


def test_an_expansion_never_outranks_the_word_that_was_typed(tmp_path: Path) -> None:
    """A rarer inflection is not stronger evidence than the query term itself.

    Scored on its own IDF, `backscattering` in one note beats `backscatter` in
    thirty even at half weight, and every exact title match sinks below it.
    """
    papers = tmp_path / "papers"
    for index in range(8):
        write_note(
            papers,
            f"Common {index}",
            key=f"a20{index}0common",
            abstract="backscatter",
        )
    write_note(papers, "Rare inflection", key="a2090rare", abstract="backscattering")
    records = search.load(papers)

    hits = search.rank(records, "backscatter", expand=True)

    assert hits[-1].record.cite_key == "a2090rare"
