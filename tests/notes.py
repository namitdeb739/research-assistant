"""The note template every test vault is built from.

A plain module, not a ``conftest.py``: the suite has no conftest by convention,
and three test files want the same template. Copying it three times would mean
three places to update whenever the frontmatter grows a key.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

NOTE = """---
title: {title}
cite_key: {key}
entry_type: inproceedings
{authors}
year: {year}
venue: {venue}
volume: null
number: null
pages: null
publisher: null
editors: []
month: null
doi: {doi}
openalex_id: null
url: null
pdf: {pdf}
pdf_url: {pdf_url}
code_url: null
citations: {citations}
open_access: null
retracted: {retracted}
{topics}
{cites}
{tags}
---

## Abstract

{abstract}

## Notes

"""


def write_note(
    papers_dir: Path,
    name: str,
    *,
    key: str,
    title: str | None = None,
    authors: tuple[str, ...] = ("Ada Lovelace", "Alan Turing", "Grace Hopper"),
    year: int = 2020,
    venue: str = "SenSys",
    doi: str | None = None,
    citations: int | None = 10,
    abstract: str = "",
    topics: tuple[str, ...] = (),
    cites: tuple[str, ...] = (),
    tags: tuple[str, ...] = ("paper",),
    pdf: str | None = None,
    pdf_url: str | None = None,
    retracted: str | None = None,
) -> Path:
    """Write one note shaped exactly like the ones `just paper` produces."""

    def block(key: str, items: tuple[str, ...], *, link: bool = False) -> str:
        """A YAML list property: inline when empty, one item per line when not."""
        if not items:
            return f"{key}: []"
        rendered = "\n".join(f"- '[[{i}]]'" if link else f"- {i}" for i in items)
        return f"{key}:\n{rendered}"

    papers_dir.mkdir(parents=True, exist_ok=True)
    path = papers_dir / f"{name}.md"
    path.write_text(
        NOTE.format(
            title=title if title is not None else name,
            key=key,
            authors=block("authors", authors),
            year=year,
            venue=venue,
            doi=doi or "null",
            pdf=f"'[[{pdf}]]'" if pdf else "null",
            pdf_url=pdf_url or "null",
            retracted=retracted or "null",
            citations="null" if citations is None else citations,
            topics=block("topics", topics, link=True),
            cites=block("cites", cites, link=True),
            tags=block("tags", tags),
            abstract=abstract,
        ),
        encoding="utf-8",
    )
    return path
