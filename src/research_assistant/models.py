"""Bibliographic record shared by the Crossref, OpenAlex, Notion and BibTeX layers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_STOPWORDS = frozenset(
    {"a", "an", "the", "on", "of", "for", "and", "in", "to", "towards", "with"}
)


def _slug(value: str) -> str:
    return _NON_ALNUM.sub("", value.lower())


@dataclass(frozen=True, slots=True)
class Paper:
    """A single bibliographic record.

    Only ``title`` is required; everything else may be missing depending on how
    complete the upstream metadata is.
    """

    title: str
    authors: tuple[str, ...] = ()
    year: int | None = None
    doi: str | None = None
    venue: str | None = None
    abstract: str | None = None
    url: str | None = None
    citations: int | None = None
    open_access: str | None = None
    entry_type: str = "article"
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def first_author_surname(self) -> str | None:
        """Surname of the first listed author, if any."""
        if not self.authors:
            return None
        return self.authors[0].split()[-1]

    def cite_key(self) -> str:
        """Deterministic ``surnameYEARword`` key.

        Used only when Notion has not computed its own ``Cite Key`` formula, so
        that the two agree in the common case and never collide silently.
        """
        surname = _slug(self.first_author_surname or "anon") or "anon"
        year = str(self.year) if self.year is not None else "nd"
        for word in self.title.split():
            slug = _slug(word)
            if slug and slug not in _STOPWORDS:
                return f"{surname}{year}{slug}"
        return f"{surname}{year}"
