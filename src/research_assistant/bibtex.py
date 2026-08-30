"""Render :class:`Paper` records as a BibTeX file."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from earth_computers.refs.models import Paper

HEADER = (
    "% Generated from the Obsidian “Research Resources” notes "
    "— do not hand-edit.\n"
    "% Regenerate with: just bib\n"
)

# Characters that would otherwise start a LaTeX command or group.
_ESCAPES = str.maketrans(
    {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "$": r"\$",
        "&": r"\&",
        "%": r"\%",
        "#": r"\#",
        "_": r"\_",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
)


# Which field carries the venue depends on the entry type, and biblatex silently
# drops the wrong one: a talk has no journal, a chapter's venue is its book, and
# a book's is its series.
_VENUE_FIELD = {
    "inproceedings": "booktitle",
    "incollection": "booktitle",
    "book": "series",
    "misc": "howpublished",
}


def escape(value: str) -> str:
    """Escape LaTeX special characters in a field value."""
    return value.translate(_ESCAPES)


def render_entry(paper: Paper, key: str) -> str:
    """Render a single BibTeX entry."""
    fields: list[tuple[str, str]] = [("title", escape(paper.title))]
    if paper.authors:
        fields.append(("author", escape(" and ".join(paper.authors))))
    if paper.year is not None:
        fields.append(("year", str(paper.year)))
    if paper.venue:
        fields.append(
            (_VENUE_FIELD.get(paper.entry_type, "journal"), escape(paper.venue))
        )
    if paper.doi:
        fields.append(("doi", paper.doi))
    if paper.url:
        # url is written verbatim: biblatex handles it, and escaping breaks links.
        fields.append(("url", paper.url))
    for name, value in sorted(paper.extra.items()):
        fields.append((name, escape(value)))

    width = max(len(name) for name, _ in fields)
    body = ",\n".join(f"  {name.ljust(width)} = {{{value}}}" for name, value in fields)
    return f"@{paper.entry_type}{{{key},\n{body}\n}}\n"


def render(entries: Iterable[tuple[Paper, str]]) -> str:
    """Render a full ``.bib`` file from ``(paper, cite_key)`` pairs."""
    ordered = sorted(entries, key=lambda pair: pair[1].lower())
    blocks = [render_entry(paper, key) for paper, key in ordered]
    return HEADER + "\n" + "\n".join(blocks)
