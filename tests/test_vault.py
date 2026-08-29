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
