"""Corpus maintenance: retractions, metadata drift, and preprint/VoR pairs.

Citing a retracted paper in a thesis is a career-grade error, and nothing in the
vault checked. Duplicate preprint/version-of-record pairs are the other thing
that quietly rots a corpus kept over years.

Everything here is read-only. The one thing `--fix` writes is the `retracted`
key, because a retraction has an unambiguous upstream answer. Drift is a
judgement -- Crossref's `container-title` for an ACM paper flips between the
full proceedings name and `SenSys '23` depending on who last deposited, and
silently rewriting the venue silently rewrites your bibliography -- and
resolving a duplicate is a deletion, which no rule should make on your behalf.

The Crossref parsing itself lives in :mod:`research_assistant.sources`, beside
the other functions that read one field out of a Crossref message, because
``build_paper`` needs it too.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from research_assistant import search, sources
from research_assistant.http import get_with_retry

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import httpx

CROSSREF_API: Final = "https://api.crossref.org/works"
# 40 rather than 50, to keep the encoded filter URL under about 2 kB.
CROSSREF_BATCH: Final = 40
CROSSREF_SELECT: Final = "DOI,title,container-title,type,issued,updated-by,relation"

# Containment, not Jaccard. A version of record that gained a subtitle is the
# commonest shape of this pair -- "Foo Bar" against "Foo Bar: An Empirical
# Study" -- and Jaccard scores it 2/4, penalising exactly the growth that
# identifies it. Containment scores it 1.0.
DUPLICATE_THRESHOLD: Final = 0.8
# Containment is trivially 1.0 when the shorter title is a couple of tokens:
# "Mementos" is contained in everything.
MIN_TITLE_TOKENS: Final = 4
YEAR_WINDOW: Final = 2

PREPRINT_PREFIXES: Final[tuple[str, ...]] = (
    "10.48550/",
    "10.1101/",
    "10.21203/",
    "10.31234/",
)
PREPRINT_VENUES: Final[tuple[str, ...]] = ("arxiv", "biorxiv", "medrxiv", "ssrn")


@dataclass(frozen=True, slots=True)
class Drift:
    """One field where the note and Crossref no longer agree."""

    cite_key: str
    field: str
    in_note: str
    upstream: str


@dataclass(frozen=True, slots=True)
class Duplicate:
    """Two notes that look like the same work at two stages of publication."""

    preprint: search.Record
    version_of_record: search.Record
    containment: float
    jaccard: float
    evidence: str


@dataclass(frozen=True, slots=True)
class Report:
    """What one pass found."""

    checked: int
    unchecked: tuple[str, ...]
    retracted: tuple[tuple[search.Record, str], ...]
    corrections: tuple[tuple[search.Record, sources.Notice], ...]
    drift: tuple[Drift, ...]
    duplicates: tuple[Duplicate, ...]

    @property
    def clean(self) -> bool:
        return not (self.retracted or self.corrections or self.drift or self.duplicates)


def fetch_records(
    dois: Sequence[str], *, client: httpx.Client
) -> dict[str, dict[str, Any]]:
    """Crossref messages for many DOIs, by lowercased DOI.

    The query route wraps its results in ``message.items``, unlike the
    single-work route ``sources.fetch_crossref`` reads.
    """
    found: dict[str, dict[str, Any]] = {}
    ordered = sorted({doi.lower() for doi in dois if doi})
    for start in range(0, len(ordered), CROSSREF_BATCH):
        batch = ordered[start : start + CROSSREF_BATCH]
        response = get_with_retry(
            CROSSREF_API,
            client=client,
            params={
                "filter": ",".join(f"doi:{doi}" for doi in batch),
                "select": CROSSREF_SELECT,
                "rows": "100",
            },
        )
        response.raise_for_status()
        message = response.json().get("message")
        if not isinstance(message, dict):
            continue
        for item in message.get("items", []) or []:
            if isinstance(item, dict) and item.get("DOI"):
                found[str(item["DOI"]).lower()] = item
    return found


def _text(value: Any) -> str:
    return sources.unescape(" ".join(str(value or "").split()))


def drift_of(record: search.Record, work: Mapping[str, Any]) -> tuple[Drift, ...]:
    """Fields where the note disagrees with Crossref, normalisation aside.

    Compared after entity resolution and whitespace collapsing, so a note that
    has been through ``tidy`` reports no drift for an ampersand.
    """
    pairs = (
        # Structural first, then editorial: an entry type or a year is cheap to
        # accept, a retitled paper is a decision.
        (
            "entry_type",
            record.entry_type,
            # Only when Crossref actually says: `_ENTRY_TYPES` falls back to
            # "article", and a default is not upstream knowledge.
            sources._ENTRY_TYPES.get(str(work["type"]), "article")
            if work.get("type")
            else "",
        ),
        ("year", str(record.year or ""), str(sources._crossref_year(dict(work)) or "")),
        ("title", record.title, sources._first(work.get("title")) or ""),
        (
            "venue",
            record.venue or "",
            sources._first(work.get("container-title")) or "",
        ),
    )
    found: list[Drift] = []
    for field, mine, theirs in pairs:
        if not theirs:
            continue  # Crossref not knowing a field is not drift
        if _text(mine).casefold() != _text(theirs).casefold():
            found.append(
                Drift(
                    cite_key=record.cite_key,
                    field=field,
                    in_note=_text(mine),
                    upstream=_text(theirs),
                )
            )
    return tuple(found)


def normalise_title(title: str) -> tuple[str, ...]:
    """Case-folded, punctuation-stripped, stopword-stripped title tokens.

    Reuses the search tokeniser rather than writing a second one: it already
    drops the large stopword list, which matters in the false-positive
    direction, since without it two unrelated titles share "of" and "the".
    """
    return tuple(search.tokenize(title))


def title_similarity(left: str, right: str) -> tuple[float, float]:
    """``(containment, jaccard)``. Containment decides; Jaccard is evidence."""
    a, b = set(normalise_title(left)), set(normalise_title(right))
    if not a or not b:
        return 0.0, 0.0
    shared = len(a & b)
    return shared / min(len(a), len(b)), shared / len(a | b)


def _surname(record: search.Record) -> str:
    return record.authors[0].split()[-1].casefold() if record.authors else ""


def is_preprint(record: search.Record) -> bool:
    """Whether a note looks like the preprint side of a pair."""
    doi = (record.doi or "").lower()
    venue = (record.venue or "").casefold()
    return (
        doi.startswith(PREPRINT_PREFIXES)
        or any(name in venue for name in PREPRINT_VENUES)
        or record.entry_type == "misc"
    )


def find_duplicates(
    records: Sequence[search.Record],
    *,
    threshold: float = DUPLICATE_THRESHOLD,
    linked: Mapping[str, str] | None = None,
) -> tuple[Duplicate, ...]:
    """Pairs that look like one work at two stages, blocked by author and year."""
    buckets: dict[tuple[str, int], list[search.Record]] = collections.defaultdict(list)
    for record in records:
        if record.year is None:
            continue
        for year in range(record.year - YEAR_WINDOW, record.year + YEAR_WINDOW + 1):
            buckets[(_surname(record), year)].append(record)

    seen: set[tuple[str, str]] = set()
    found: list[Duplicate] = []
    for bucket in buckets.values():
        for index, left in enumerate(bucket):
            for right in bucket[index + 1 :]:
                pair = tuple(sorted((left.cite_key, right.cite_key)))
                if pair in seen or left.cite_key == right.cite_key:
                    continue
                seen.add((pair[0], pair[1]))
                by_crossref = bool(
                    linked
                    and left.doi
                    and right.doi
                    and linked.get(left.doi.lower()) == right.doi.lower()
                )
                shorter = min(
                    len(normalise_title(left.title)), len(normalise_title(right.title))
                )
                if not by_crossref and shorter < MIN_TITLE_TOKENS:
                    continue
                containment, jaccard = title_similarity(left.title, right.title)
                if not by_crossref and containment < threshold:
                    continue
                # Order the pair so the recommendation is obvious.
                first, second = (left, right) if is_preprint(left) else (right, left)
                found.append(
                    Duplicate(
                        preprint=first,
                        version_of_record=second,
                        containment=containment,
                        jaccard=jaccard,
                        evidence=(
                            "crossref preprint relation"
                            if by_crossref
                            else f"title containment {containment:.2f}, "
                            f"jaccard {jaccard:.2f}"
                        ),
                    )
                )
    return tuple(found)


def check(
    records: Sequence[search.Record],
    *,
    client: httpx.Client,
    retractions: bool = True,
    drift: bool = True,
    duplicates: bool = True,
) -> Report:
    """One pass over the vault. Five Crossref requests for a 180-note corpus."""
    with_doi = [record for record in records if record.doi]
    unchecked = tuple(sorted(record.cite_key for record in records if not record.doi))

    works: dict[str, dict[str, Any]] = {}
    if (retractions or drift) and with_doi:
        works = fetch_records([r.doi or "" for r in with_doi], client=client)

    found_retracted: list[tuple[search.Record, str]] = []
    found_corrections: list[tuple[search.Record, sources.Notice]] = []
    found_drift: list[Drift] = []
    for record in with_doi:
        work = works.get((record.doi or "").lower())
        if work is None:
            continue
        if retractions and not sources.is_notice(work):
            notices = sources.notices(work)
            kind = sources.strongest(notices)
            if kind:
                found_retracted.append((record, kind))
            found_corrections.extend(
                (record, notice)
                for notice in notices
                if notice.kind in sources.NOTICE_TYPES
            )
        if drift:
            found_drift.extend(drift_of(record, work))

    linked: dict[str, str] = {}
    for doi, work in works.items():
        relation = work.get("relation")
        if not isinstance(relation, dict):
            continue
        for key in ("is-preprint-of", "has-preprint"):
            for entry in relation.get(key, []) or []:
                if isinstance(entry, dict) and entry.get("id"):
                    linked[doi] = str(entry["id"]).lower()

    return Report(
        checked=len(works),
        unchecked=unchecked,
        retracted=tuple(found_retracted),
        corrections=tuple(found_corrections),
        drift=tuple(found_drift),
        duplicates=find_duplicates(records, linked=linked) if duplicates else (),
    )
