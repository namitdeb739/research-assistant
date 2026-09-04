"""Bibliographic record shared by the Crossref, OpenAlex, vault and BibTeX layers."""

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
    # What a bibliography renders beyond the venue. Crossref has all of these
    # and none of them reached the note, so every generated @article came out
    # without a volume, issue or page range.
    volume: str | None = None
    number: str | None = None
    pages: str | None = None
    publisher: str | None = None
    editors: tuple[str, ...] = ()
    month: str | None = None
    abstract: str | None = None
    url: str | None = None
    citations: int | None = None
    open_access: str | None = None
    # Crossref's strongest ``updated-by`` notice, or None. Intrinsic, derived,
    # and retractable: nobody sets it by hand and a withdrawn notice clears it.
    retracted: str | None = None
    pdf_url: str | None = None
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

        Written into the note's ``cite_key`` frontmatter at creation time and
        read back from there, so the key in ``refs.bib`` is a recorded fact
        rather than something recomputed on every run.
        """
        surname = _slug(self.first_author_surname or "anon") or "anon"
        year = str(self.year) if self.year is not None else "nd"
        for word in self.title.split():
            slug = _slug(word)
            if slug and slug not in _STOPWORDS:
                return f"{surname}{year}{slug}"
        return f"{surname}{year}"
