"""Render :class:`Paper` records as a BibTeX file."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from research_assistant.models import Paper


class BibtexError(Exception):
    """Raised when the notes cannot produce a usable bibliography."""


HEADER = (
    "% Generated from the Obsidian “Research Resources” notes "
    "— do not hand-edit.\n"
    "% Regenerate with the `bib` command.\n"
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

# Which of the bibliographic fields an entry type actually renders. A journal
# article has a volume and an issue; a conference paper has neither and does
# have a publisher. Emitting all of them everywhere is how a bibliography ends
# up with `number = {}` on a talk.
_TYPE_FIELDS: dict[str, tuple[str, ...]] = {
    "article": ("volume", "number", "pages"),
    "inproceedings": ("pages", "publisher", "editors"),
    "incollection": ("volume", "pages", "publisher", "editors"),
    "book": ("volume", "publisher", "editors"),
    "phdthesis": ("publisher",),
    "mastersthesis": ("publisher",),
    "techreport": ("number", "publisher"),
    "misc": (),
}
_DEFAULT_FIELDS: tuple[str, ...] = ("volume", "number", "pages", "publisher")


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
    if paper.month:
        fields.append(("month", paper.month))
    for name in _TYPE_FIELDS.get(paper.entry_type, _DEFAULT_FIELDS):
        if name == "editors":
            if paper.editors:
                fields.append(("editor", escape(" and ".join(paper.editors))))
            continue
        value = getattr(paper, name)
        if value:
            fields.append((name, escape(str(value))))
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


def duplicate_keys(entries: Iterable[tuple[Paper, str]]) -> dict[str, list[Paper]]:
    """Cite keys claimed by more than one paper, in the order they were read."""
    claimed: dict[str, list[Paper]] = {}
    for paper, key in entries:
        claimed.setdefault(key, []).append(paper)
    return {key: papers for key, papers in claimed.items() if len(papers) > 1}


def render(entries: Iterable[tuple[Paper, str]]) -> str:
    """Render a full ``.bib`` file from ``(paper, cite_key)`` pairs."""
    ordered = sorted(entries, key=lambda pair: pair[1].lower())
    repeated = duplicate_keys(ordered)
    if repeated:
        # Both entries would be written and BibTeX would keep one of them, so a
        # citation would silently point at the wrong paper. Renaming here is not
        # the fix: a cite key is a recorded fact, and inventing one at render
        # time would make the bibliography disagree with the vault.
        shown = "; ".join(
            f"{key} ({', '.join(paper.title for paper in papers)})"
            for key, papers in sorted(repeated.items())
        )
        raise BibtexError(
            f"{len(repeated)} cite key(s) claimed by more than one note: {shown}. "
            f"Give one of them a different key."
        )
    blocks = [render_entry(paper, key) for paper, key in ordered]
    return HEADER + "\n" + "\n".join(blocks)
