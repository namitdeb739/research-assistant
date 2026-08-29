from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from earth_computers.refs import vault
from earth_computers.refs.models import Paper

if TYPE_CHECKING:
    from pathlib import Path

PAPER = Paper(
    title="Soil-Powered Computing: A Guide",
    authors=("Jane Yen", "Bill Zhao"),
    year=2023,
    doi="10.1145/3596262",
    venue="IMWUT",
    abstract="Soil microbial fuel cells power sensing.",
    url="https://doi.org/10.1145/3596262",
    citations=41,
    open_access="Gold",
    entry_type="inproceedings",
)


def test_note_is_named_for_its_title(tmp_path: Path) -> None:
    path = vault.create_paper(PAPER, "yen2023soil", papers_dir=tmp_path)

    # The colon becomes a dash: Obsidian rejects it in a file name.
    assert path.name == "Soil-Powered Computing - A Guide.md"
    # ...but the title property keeps it verbatim, and that is what BibTeX reads.
    paper, _ = vault.read_all(papers_dir=tmp_path)[0]
    assert paper.title == "Soil-Powered Computing: A Guide"


def test_note_name_strips_every_forbidden_character() -> None:
    messy = 'TCP/IP: "C" <d>|e?f*g\\h#i^j[k]'

    # Slashes keep the words apart; the rest are dropped outright.
    assert vault.note_name(messy, "key") == "TCP-IP - C defg-hijk"


def test_note_name_falls_back_to_the_cite_key() -> None:
    assert vault.note_name("///", "yen2023soil") == "yen2023soil"


def test_round_trip_preserves_the_record(tmp_path: Path) -> None:
    key = PAPER.cite_key()
    vault.create_paper(PAPER, key, papers_dir=tmp_path)

    entries = vault.read_all(papers_dir=tmp_path)

    assert len(entries) == 1
    paper, read_key = entries[0]
    assert read_key == key
    # The abstract lives in the body, so it does not survive the round trip.
    assert paper == Paper(
        title=PAPER.title,
        authors=PAPER.authors,
        year=PAPER.year,
        doi=PAPER.doi,
        venue=PAPER.venue,
        url=PAPER.url,
        citations=PAPER.citations,
        open_access="gold",
        entry_type=PAPER.entry_type,
    )


def test_title_with_a_colon_survives_yaml(tmp_path: Path) -> None:
    vault.create_paper(PAPER, "yen2023soil", papers_dir=tmp_path)

    paper, _ = vault.read_all(papers_dir=tmp_path)[0]

    assert paper.title == "Soil-Powered Computing: A Guide"


def test_abstract_is_written_to_the_body(tmp_path: Path) -> None:
    path = vault.create_paper(PAPER, "yen2023soil", papers_dir=tmp_path)

    text = path.read_text(encoding="utf-8")

    assert "## Abstract\n\nSoil microbial fuel cells power sensing." in text


def test_find_by_doi(tmp_path: Path) -> None:
    path = vault.create_paper(PAPER, "yen2023soil", papers_dir=tmp_path)

    assert vault.find_by_doi("10.1145/3596262", papers_dir=tmp_path) == path
    assert vault.find_by_doi("10.1145/nope", papers_dir=tmp_path) is None


def test_create_refuses_to_clobber(tmp_path: Path) -> None:
    vault.create_paper(PAPER, "yen2023soil", papers_dir=tmp_path)

    with pytest.raises(vault.VaultError, match="refusing to overwrite"):
        vault.create_paper(PAPER, "yen2023soil", papers_dir=tmp_path)


def test_frontmatter_holds_only_intrinsic_properties(tmp_path: Path) -> None:
    path = vault.create_paper(PAPER, "yen2023soil", papers_dir=tmp_path)

    front = path.read_text(encoding="utf-8").split("---")[1]

    # Judgement and progress are prose in the body, never properties.
    for judgement in ("status:", "relevance:", "rating:", "section:", "read_on:"):
        assert judgement not in front


def test_pdf_is_embedded_when_one_was_saved(tmp_path: Path) -> None:
    path = vault.create_paper(
        PAPER, "yen2023soil", papers_dir=tmp_path, pdf_name="yen2023soil.pdf"
    )

    text = path.read_text(encoding="utf-8")

    assert 'pdf: "[[yen2023soil.pdf]]"' in text or "pdf: '[[yen2023soil.pdf]]'" in text
    assert "## PDF\n\n![[yen2023soil.pdf]]" in text


def test_no_pdf_section_without_a_pdf(tmp_path: Path) -> None:
    path = vault.create_paper(PAPER, "yen2023soil", papers_dir=tmp_path)

    assert "## PDF" not in path.read_text(encoding="utf-8")


def test_save_pdf_rejects_a_block_page(tmp_path: Path) -> None:
    with pytest.raises(vault.VaultError, match="not a PDF"):
        vault.save_pdf("yen2023soil", b"<html>403</html>", pdfs_dir=tmp_path)


def test_save_pdf_writes_real_pdf_bytes(tmp_path: Path) -> None:
    path = vault.save_pdf("yen2023soil", b"%PDF-1.7\nbody", pdfs_dir=tmp_path)

    assert path.name == "yen2023soil.pdf"
    assert path.read_bytes().startswith(b"%PDF-")


def test_read_all_rejects_a_missing_folder(tmp_path: Path) -> None:
    with pytest.raises(vault.VaultError, match="does not exist"):
        vault.read_all(papers_dir=tmp_path / "absent")


def test_split_frontmatter_keeps_a_horizontal_rule_in_the_body() -> None:
    """A ``---`` rule in the prose is body, not a second delimiter.

    Splitting on it too would silently truncate the note, which mattered the
    moment ``update_frontmatter`` began reattaching the body it reads here.
    """
    note = "---\ntitle: x\n---\n\n## Notes\n\nBefore.\n\n---\n\nAfter.\n"

    front, body = vault._split_frontmatter(note)

    assert front == {"title": "x"}
    assert body == "## Notes\n\nBefore.\n\n---\n\nAfter.\n"


def test_update_frontmatter_preserves_the_body_byte_for_byte(tmp_path: Path) -> None:
    path = vault.create_paper(PAPER, "yen2023soil", papers_dir=tmp_path)
    original_body = vault._split_frontmatter(path.read_text(encoding="utf-8"))[1]

    assert vault.update_frontmatter(path, {"cites": ["[[Another Paper]]"]})

    front, body = vault._split_frontmatter(path.read_text(encoding="utf-8"))
    assert body == original_body
    assert front["cites"] == ["[[Another Paper]]"]


def test_update_frontmatter_preserves_hand_written_prose(tmp_path: Path) -> None:
    """The linker must never eat notes the user has since written."""
    path = vault.create_paper(PAPER, "yen2023soil", papers_dir=tmp_path)
    text = path.read_text(encoding="utf-8")
    _, body = vault._split_frontmatter(text)
    enriched = body + "\nMy own take.\n\n---\n\nAnd a footnote.\n"
    path.write_text(text.replace(body, enriched), encoding="utf-8")

    vault.update_frontmatter(path, {"topics": ["Soil"]})

    assert "My own take." in path.read_text(encoding="utf-8")
    assert "And a footnote." in path.read_text(encoding="utf-8")


def test_update_frontmatter_preserves_key_order(tmp_path: Path) -> None:
    path = vault.create_paper(PAPER, "yen2023soil", papers_dir=tmp_path)
    before = list(vault.read_frontmatter(path))

    vault.update_frontmatter(path, {"topics": ["Soil"], "cites": []})

    assert list(vault.read_frontmatter(path)) == before


def test_update_frontmatter_leaves_other_keys_alone(tmp_path: Path) -> None:
    path = vault.create_paper(PAPER, "yen2023soil", papers_dir=tmp_path)

    vault.update_frontmatter(path, {"topics": ["Soil"]})

    front = vault.read_frontmatter(path)
    assert front["title"] == PAPER.title
    assert front["cite_key"] == "yen2023soil"
    assert front["doi"] == PAPER.doi


def test_update_frontmatter_is_a_noop_when_nothing_changes(tmp_path: Path) -> None:
    path = vault.create_paper(PAPER, "yen2023soil", papers_dir=tmp_path)

    assert vault.update_frontmatter(path, {"topics": []}) is False


def test_update_frontmatter_appends_an_absent_key(tmp_path: Path) -> None:
    path = vault.create_paper(PAPER, "yen2023soil", papers_dir=tmp_path)

    assert vault.update_frontmatter(path, {"openalex_id": "W42"})
    assert vault.read_frontmatter(path)["openalex_id"] == "W42"


def test_create_paper_records_openalex_id_topics_and_tags(tmp_path: Path) -> None:
    path = vault.create_paper(
        PAPER,
        "yen2023soil",
        papers_dir=tmp_path,
        openalex_id="W4390812365",
        topics=["Microbial Fuel Cells and Bioremediation"],
        tags=["paper", "harvested", "topic/environmental-engineering"],
    )

    front = vault.read_frontmatter(path)
    assert front["openalex_id"] == "W4390812365"
    assert front["topics"] == ["Microbial Fuel Cells and Bioremediation"]
    assert front["tags"] == ["paper", "harvested", "topic/environmental-engineering"]


def test_create_paper_defaults_stay_unopinionated(tmp_path: Path) -> None:
    """Adding one paper by hand must look exactly as it did before."""
    path = vault.create_paper(PAPER, "yen2023soil", papers_dir=tmp_path)

    front = vault.read_frontmatter(path)
    assert front["topics"] == []
    assert front["cites"] == []
    assert front["tags"] == ["paper"]
    assert front["openalex_id"] is None


def test_index_builds_both_lookups(tmp_path: Path) -> None:
    path = vault.create_paper(
        PAPER, "yen2023soil", papers_dir=tmp_path, openalex_id="W1"
    )

    by_doi, by_openalex = vault.index(tmp_path)

    assert by_doi[PAPER.doi or ""] == path
    assert by_openalex["W1"] == path


def test_index_of_a_missing_folder_is_empty(tmp_path: Path) -> None:
    assert vault.index(tmp_path / "absent") == ({}, {})


def test_topic_link_is_a_wikilink_not_a_label() -> None:
    """The whole point: a plain string is not a node in Obsidian's graph."""
    assert vault.topic_link("Microbial Fuel Cells") == "[[Microbial Fuel Cells]]"


def test_topic_link_sanitises_a_filename_hostile_topic() -> None:
    """A topic with a colon or slash must still resolve to a real note."""
    assert vault.topic_link("Sensing: Soil/Water") == "[[Sensing - Soil-Water]]"


def test_write_topic_hub_lists_papers_newest_first(tmp_path: Path) -> None:
    path = vault.write_topic_hub(
        "Soil Power",
        [("Older Paper", 2011, 40), ("Newer Paper", 2024, None)],
        topics_dir=tmp_path,
    )

    body = vault._split_frontmatter(path.read_text(encoding="utf-8"))[1]
    assert body.index("[[Newer Paper]]") < body.index("[[Older Paper]]")
    assert "(2024)" in body
    assert "— 40 citations" in body


def test_write_topic_hub_is_tagged_and_counted(tmp_path: Path) -> None:
    path = vault.write_topic_hub(
        "Soil Power", [("A", 2020, 1), ("B", 2021, 2)], topics_dir=tmp_path
    )

    front = vault.read_frontmatter(path)
    assert front["tags"] == ["topic"]
    assert front["papers"] == 2
    assert front["title"] == "Soil Power"


def test_write_topic_hub_regenerates_wholesale(tmp_path: Path) -> None:
    """A topic losing a paper must not leave a stale link behind."""
    vault.write_topic_hub(
        "Soil Power", [("A", 2020, 1), ("Dropped", 2019, 1)], topics_dir=tmp_path
    )
    path = vault.write_topic_hub("Soil Power", [("A", 2020, 1)], topics_dir=tmp_path)

    assert "[[Dropped]]" not in path.read_text(encoding="utf-8")
    assert vault.read_frontmatter(path)["papers"] == 1
