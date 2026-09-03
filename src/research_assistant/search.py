"""Ranked search over the paper notes — the read side of the vault.

The corpus is 177 notes and a third of a megabyte of prose, which is too much to
read whole and too varied for a substring grep: a query for "intermittent
computing" should reach Mementos through its abstract, not just the two notes
with the words in their title.

So this ranks. BM25 over a weighted bag of words per note, with no index, no
embedding and no model in the loop — the same standard the rest of the refs
tooling holds itself to. A full pass over the vault costs a few tens of
milliseconds, which is cheaper than keeping an index honest.

Everything here is read-only. Notes are write-once and Obsidian owns them.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from earth_computers.refs import vault

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

# Query terms are matched as written: no stemmer. A suffix stripper turns
# "backscatter" and "backscattering" into one term, but also "bio" and "bios",
# and this vocabulary is full of coined words a general stemmer mangles. Terms
# are OR-ed instead, so a near-miss still ranks rather than dropping the note.
_WORD = re.compile(r"[a-z0-9]+")

# Deliberately not ``models._STOPWORDS``: that list exists to pick the first
# meaningful word of a title for a cite key, so it is tiny. Search needs the
# words that appear in every abstract ever written, or they dominate nothing
# and merely cost time. IDF would discount them anyway; dropping them early
# keeps the snippet picker from centring on "the".
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "then",
        "than",
        "that",
        "this",
        "these",
        "those",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "from",
        "by",
        "with",
        "without",
        "into",
        "over",
        "under",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "it",
        "its",
        "we",
        "our",
        "us",
        "they",
        "their",
        "he",
        "she",
        "his",
        "her",
        "you",
        "your",
        "i",
        "not",
        "no",
        "nor",
        "so",
        "such",
        "can",
        "could",
        "may",
        "might",
        "must",
        "shall",
        "should",
        "will",
        "would",
        "do",
        "does",
        "did",
        "done",
        "have",
        "has",
        "had",
        "here",
        "there",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "whose",
        "what",
        "why",
        "how",
        "all",
        "any",
        "both",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "only",
        "own",
        "same",
        "too",
        "very",
        "just",
        "also",
        "however",
        "thus",
        "hence",
        "therefore",
        "paper",
        "present",
        "presents",
        "propose",
        "proposed",
        "approach",
        "using",
        "use",
        "used",
        "uses",
        "show",
        "shows",
        "shown",
        "result",
        "results",
        "work",
        "works",
    ]
)

# Where a term is found matters as much as how often. A title is the author's
# own one-line summary of the paper, so a hit there is worth several in the
# body; a hit in the hand-written takeaway is worth as much, because it is the
# only prose in the vault that is not the publisher's.
WEIGHTS: dict[str, float] = {
    "title": 3.0,
    "topics": 2.0,
    "takeaway": 2.0,
    "abstract": 1.0,
    "notes": 1.0,
    "venue": 1.0,
    "authors": 1.0,
}

# Standard BM25 constants. k1 damps runaway term frequency, b scales the
# length normalisation — an abstract should not outrank a title for being long.
K1 = 1.5
B = 0.75

SNIPPET_WIDTH = 200
_LEAD = 40

# What marks a query term inside a snippet. Guillemets rather than ** or [] so
# the marking survives being pasted into Markdown or a shell without meaning
# anything to either.
MARK_OPEN = "⟪"
MARK_CLOSE = "⟫"

_WIKILINK = re.compile(r"\[\[(.+?)\]\]")


class SearchError(Exception):
    """Raised when a target names no note, or more than one."""


@dataclass(frozen=True, slots=True)
class Record:
    """One paper note, parsed once: properties, prose, and its PDF if present."""

    path: Path
    cite_key: str
    title: str
    authors: tuple[str, ...]
    year: int | None
    venue: str | None
    citations: int | None
    doi: str | None
    openalex_id: str | None
    open_access: str | None
    topics: tuple[str, ...]
    tags: tuple[str, ...]
    cites: tuple[str, ...]
    sections: dict[str, str]
    pdf_name: str | None
    pdf_path: Path | None
    pdf_url: str | None

    @property
    def has_pdf(self) -> bool:
        """Whether the full text is on disk and can actually be opened."""
        return self.pdf_path is not None

    @property
    def byline(self) -> str:
        """``Ransford et al.`` — enough to recognise the paper, not to cite it."""
        if not self.authors:
            return "unknown"
        first = self.authors[0].split()[-1]
        if len(self.authors) == 1:
            return first
        if len(self.authors) == 2:
            return f"{first} & {self.authors[1].split()[-1]}"
        return f"{first} et al."


@dataclass(frozen=True, slots=True)
class Hit:
    """A record that matched, with the evidence that made it match."""

    record: Record
    score: float
    field: str
    snippet: str


def _str(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def _links(value: Any) -> tuple[str, ...]:
    """Read a list property, unwrapping ``[[wikilinks]]`` to bare names."""
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        match = _WIKILINK.search(text)
        out.append(match.group(1) if match else text)
    return tuple(out)


def _record(path: Path, *, pdfs_dir: Path) -> Record | None:
    front, body = vault.read_note(path)
    title = _str(front.get("title"))
    if not title:
        return None

    raw_authors = front.get("authors")
    authors = (
        tuple(str(a).strip() for a in raw_authors if str(a).strip())
        if isinstance(raw_authors, list)
        else ()
    )

    # The note names its PDF; whether that file exists is a separate fact, and
    # conflating them is exactly what `pdf --audit` is for.
    pdf_name = None
    if claimed := _str(front.get("pdf")):
        match = _WIKILINK.search(claimed)
        pdf_name = match.group(1) if match else claimed
    pdf_path = pdfs_dir / pdf_name if pdf_name else None

    return Record(
        path=path,
        cite_key=_str(front.get("cite_key")) or path.stem,
        title=title,
        authors=authors,
        year=_int(front.get("year")),
        venue=_str(front.get("venue")),
        citations=_int(front.get("citations")),
        doi=_str(front.get("doi")),
        openalex_id=_str(front.get("openalex_id")),
        open_access=_str(front.get("open_access")),
        topics=_links(front.get("topics")),
        tags=tuple(str(t).strip() for t in front.get("tags") or [] if str(t).strip()),
        cites=_links(front.get("cites")),
        sections=vault.parse_body(body),
        pdf_name=pdf_name,
        pdf_path=pdf_path if pdf_path is not None and pdf_path.is_file() else None,
        pdf_url=_str(front.get("pdf_url")),
    )


def pdfs_dir_for(papers_dir: Path) -> Path:
    """Where the PDFs sit: a sibling of the papers folder."""
    return papers_dir.parent / vault.PDFS_DIRNAME


def load(papers_dir: Path) -> list[Record]:
    """Read every note in the vault, once."""
    if not papers_dir.is_dir():
        raise SearchError(
            f"{papers_dir} does not exist. Set VAULT_PAPERS_DIR, or create the folder."
        )
    pdfs_dir = pdfs_dir_for(papers_dir)
    records = [
        _record(path, pdfs_dir=pdfs_dir) for path in sorted(papers_dir.glob("*.md"))
    ]
    return [record for record in records if record is not None]


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric runs, minus stopwords and single characters."""
    return [
        token
        for token in _WORD.findall(text.lower())
        if len(token) > 1 and token not in _STOPWORDS
    ]


def fields(record: Record) -> dict[str, str]:
    """The searchable text of a note, by field name. Keys match :data:`WEIGHTS`."""
    return {
        "title": record.title,
        "topics": " ".join(record.topics),
        "takeaway": record.sections.get("Key takeaway", ""),
        "abstract": record.sections.get("Abstract", ""),
        "notes": record.sections.get("Notes", ""),
        "venue": record.venue or "",
        "authors": " ".join(record.authors),
    }


def _bag(record: Record) -> dict[str, float]:
    """Weighted term frequencies for one note."""
    bag: dict[str, float] = {}
    for name, text in fields(record).items():
        weight = WEIGHTS[name]
        for token in tokenize(text):
            bag[token] = bag.get(token, 0.0) + weight
    return bag


def _mark(text: str, terms: Iterable[str]) -> str:
    """Wrap each query term in the snippet so the match is visible at a glance."""
    wanted = {term for term in terms if term}
    if not wanted:
        return text
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(term) for term in sorted(wanted)) + r")",
        re.IGNORECASE,
    )
    return pattern.sub(lambda m: f"{MARK_OPEN}{m.group(0)}{MARK_CLOSE}", text)


def _densest(text: str, wanted: set[str]) -> str | None:
    """The ``SNIPPET_WIDTH`` window of ``text`` holding the most query terms."""
    spans = [
        match.span()
        for match in _WORD.finditer(text.lower())
        if match.group(0) in wanted
    ]
    if not spans:
        return None

    best_start, best_count = spans[0][0], 0
    for start, _ in spans:
        count = sum(1 for other, _ in spans if start <= other < start + SNIPPET_WIDTH)
        if count > best_count:
            best_start, best_count = start, count

    left = max(0, best_start - _LEAD)
    right = min(len(text), left + SNIPPET_WIDTH)
    # Do not cut a word in half at either end.
    if left > 0 and (space := text.find(" ", left)) != -1 and space < best_start:
        left = space + 1
    if right < len(text) and (space := text.rfind(" ", left, right)) > left:
        right = space

    window = " ".join(text[left:right].split())
    lead = "…" if left > 0 else ""
    trail = "…" if right < len(text) else ""
    return f"{lead}{_mark(window, wanted)}{trail}"


def best_snippet(record: Record, terms: Sequence[str]) -> tuple[str, str]:
    """``(field, snippet)`` for the most informative match on this record.

    Ordered by how much the field tells a reader, not by the field weights: a
    title match is worth more to the *ranking*, but echoing the title back as a
    snippet under the title is useless, so prose wins where there is any.
    """
    wanted = set(terms)
    texts = fields(record)
    for name in ("takeaway", "abstract", "notes", "topics", "venue", "title"):
        if not texts[name]:
            continue
        if (window := _densest(texts[name], wanted)) is not None:
            return name, window
    return "", ""


def rank(records: Sequence[Record], query: str) -> list[Hit]:
    """Score every record against the query, best first. OR over the terms."""
    terms = tokenize(query)
    if not terms or not records:
        return []

    bags = [_bag(record) for record in records]
    lengths = [sum(bag.values()) for bag in bags]
    total = len(records)
    average = sum(lengths) / total or 1.0
    unique = set(terms)
    document_frequency = {
        term: sum(1 for bag in bags if term in bag) for term in unique
    }

    hits: list[Hit] = []
    for record, bag, length in zip(records, bags, lengths, strict=True):
        score = 0.0
        for term in unique:
            frequency = bag.get(term, 0.0)
            if not frequency:
                continue
            seen = document_frequency[term]
            idf = math.log(1 + (total - seen + 0.5) / (seen + 0.5))
            score += (
                idf
                * (frequency * (K1 + 1))
                / (frequency + K1 * (1 - B + B * length / average))
            )
        if score > 0:
            field, snippet = best_snippet(record, terms)
            hits.append(Hit(record, score, field, snippet))

    hits.sort(key=lambda hit: (-hit.score, hit.record.title.lower()))
    return hits


def by_citations(records: Sequence[Record]) -> list[Hit]:
    """The no-query ordering: most cited first, unknown counts last.

    A missing citation count is not a zero — ``yen2023soilpowered`` has none
    recorded while ``boyle1993backscatter`` genuinely has none — so the two sort
    apart rather than into one indistinguishable block at the bottom.
    """
    ordered = sorted(
        records,
        key=lambda r: (r.citations is None, -(r.citations or 0), r.title.lower()),
    )
    return [Hit(record, 0.0, "", "") for record in ordered]


def _matches(needle: str | None, haystack: str | None) -> bool:
    return needle is None or (
        haystack is not None and needle.lower() in haystack.lower()
    )


def apply_filters(
    records: Sequence[Record],
    *,
    topic: str | None = None,
    tag: str | None = None,
    venue: str | None = None,
    min_year: int | None = None,
    max_year: int | None = None,
    min_citations: int | None = None,
    has_pdf: bool | None = None,
) -> list[Record]:
    """Narrow the corpus before ranking.

    Filtering first is what makes an ambiguous term usable: the vault holds a
    cluster of *acoustic* backscatter papers that share a word with the RF sense
    and nothing else, and no amount of ranking separates them.
    """
    kept: list[Record] = []
    for record in records:
        if topic is not None and not any(
            topic.lower() in name.lower() for name in record.topics
        ):
            continue
        if tag is not None and not any(
            tag.lower() == name.lower() or name.lower().endswith(f"/{tag.lower()}")
            for name in record.tags
        ):
            continue
        if not _matches(venue, record.venue):
            continue
        if min_year is not None and (record.year is None or record.year < min_year):
            continue
        if max_year is not None and (record.year is None or record.year > max_year):
            continue
        if min_citations is not None and (record.citations or 0) < min_citations:
            continue
        if has_pdf is not None and record.has_pdf is not has_pdf:
            continue
        kept.append(record)
    return kept


def resolve(records: Sequence[Record], target: str) -> Record:
    """Find the one note a cite key, note name, DOI or title fragment names.

    Exact identifiers are tried before fuzzy ones, so a cite key that also
    appears inside some other note's title still resolves to its own note. An
    ambiguous fragment raises rather than guessing: picking one of several
    papers silently is how the wrong thing gets cited.
    """
    wanted = target.strip().lower().removeprefix("https://doi.org/")
    stem = wanted.removesuffix(".pdf").removesuffix(".md")

    for candidates in (
        [r for r in records if r.cite_key.lower() == stem],
        [r for r in records if r.doi and r.doi.lower() == wanted],
        [r for r in records if r.path.stem.lower() == stem],
        [r for r in records if r.title.lower() == wanted],
        [
            r
            for r in records
            if stem in r.path.stem.lower()
            or stem in r.title.lower()
            or stem in r.cite_key.lower()
        ],
    ):
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            keys = sorted(r.cite_key for r in candidates)
            names = ", ".join(keys[:6]) + (", …" if len(keys) > 6 else "")
            raise SearchError(f"{target!r} matches {len(candidates)} notes: {names}")

    raise SearchError(f"{target!r} matches no note in the vault")


def resolve_pdf(records: Sequence[Record], target: str) -> Record:
    """Find the note that owns a PDF, given its filename or path.

    The reverse direction: a file is open and the question is which paper it is.
    """
    stem = target.strip().rsplit("/", 1)[-1].removesuffix(".pdf").lower()
    owners = [
        r
        for r in records
        if r.pdf_name and r.pdf_name.removesuffix(".pdf").lower() == stem
    ]
    if len(owners) == 1:
        return owners[0]
    if len(owners) > 1:
        names = ", ".join(sorted(r.cite_key for r in owners))
        raise SearchError(f"{target!r} is claimed by {len(owners)} notes: {names}")
    # No note claims it; the cite key convention is the fallback, and a hit here
    # means the note simply has not adopted the file yet (`tidy` does that).
    return resolve(records, stem)


@dataclass(frozen=True, slots=True)
class Audit:
    """How the notes and the PDF folder disagree, if they do."""

    total: int
    with_pdf: int
    orphans: tuple[str, ...]
    missing: tuple[str, ...]
    unclaimed: tuple[str, ...]
    mismatched: tuple[tuple[str, str], ...]
    without_pdf_with_url: int
    without_pdf_no_url: int

    @property
    def clean(self) -> bool:
        return not (self.orphans or self.missing or self.unclaimed or self.mismatched)


def audit(records: Sequence[Record], *, pdfs_dir: Path) -> Audit:
    """Reconcile what the notes claim against what is on disk.

    ``tidy`` adopts any PDF whose filename matches a cite key, and PDFs behind a
    publisher's bot check are saved by hand, so the two sides can drift apart in
    four distinct ways. They are all zero today; this is what keeps them so.
    """
    on_disk = (
        {path.stem: path for path in pdfs_dir.glob("*.pdf")}
        if pdfs_dir.is_dir()
        else {}
    )
    keys = {record.cite_key: record for record in records}
    claimed: dict[str, Record] = {}
    missing: list[str] = []
    mismatched: list[tuple[str, str]] = []

    for record in records:
        if record.pdf_name is None:
            continue
        stem = record.pdf_name.removesuffix(".pdf")
        claimed[stem] = record
        if stem not in on_disk:
            # A claim on a file that is not there is a missing-PDF problem, and
            # reporting it as a naming problem too would double-count one note.
            missing.append(record.cite_key)
        elif stem != record.cite_key:
            mismatched.append((record.cite_key, record.pdf_name))

    return Audit(
        total=len(records),
        with_pdf=sum(1 for record in records if record.has_pdf),
        orphans=tuple(sorted(set(on_disk) - set(keys) - set(claimed))),
        missing=tuple(sorted(missing)),
        unclaimed=tuple(sorted(set(on_disk) & set(keys) - set(claimed))),
        mismatched=tuple(sorted(mismatched)),
        without_pdf_with_url=sum(
            1 for r in records if r.pdf_name is None and r.pdf_url is not None
        ),
        without_pdf_no_url=sum(
            1 for r in records if r.pdf_name is None and r.pdf_url is None
        ),
    )


def cited_by(records: Sequence[Record], record: Record) -> list[Record]:
    """Notes in the vault whose ``cites`` names this one.

    Computed by scanning: there is deliberately no ``cited_by`` property to keep
    in sync, and Obsidian's "Linked mentions" pane is not reachable from a
    terminal.
    """
    name = record.path.stem
    return [other for other in records if name in other.cites]


def cites_in_vault(
    records: Sequence[Record], record: Record
) -> tuple[list[Record], tuple[str, ...]]:
    """``(resolved, unresolved)`` for the papers this note cites.

    The unresolved tail is the useful part: it says how much of this paper's
    bibliography ``expand`` has not pulled in yet.
    """
    by_name = {other.path.stem: other for other in records}
    resolved = [by_name[name] for name in record.cites if name in by_name]
    unresolved = tuple(name for name in record.cites if name not in by_name)
    return resolved, unresolved
