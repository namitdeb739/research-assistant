"""Deterministic metadata lookup from Crossref and OpenAlex.

No model in the loop: given a DOI or an OpenAlex id, the same record comes back
every time.
The note's prose is left empty on purpose. A judgement about a paper is yours to
write in Obsidian, and no index has one to give.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import httpx

from research_assistant.http import USER_AGENT, get_with_retry
from research_assistant.models import Paper

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

CROSSREF_API = "https://api.crossref.org/works"
OPENALEX_API = "https://api.openalex.org/works"

_JATS_TAG = re.compile(r"<[^>]+>")
_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", re.IGNORECASE)
_OPENALEX_ID = re.compile(
    r"^(?:https?://(?:api\.)?openalex\.org/(?:works/)?|openalex:)?(W\d+)$",
    re.IGNORECASE,
)


class SourceLookupError(Exception):
    """Raised when a DOI or OpenAlex id cannot be resolved."""


def normalize_doi(raw: str) -> str:
    """Strip any ``https://doi.org/`` or ``doi:`` prefix and lowercase."""
    return _DOI_PREFIX.sub("", raw.strip()).strip("/").lower()


def as_openalex_id(raw: str) -> str | None:
    """Return the bare ``W123`` if ``raw`` names an OpenAlex work, else ``None``.

    Accepts the id on its own, an ``openalex:`` prefix, and either the web or
    the API URL, so whatever the user copied out of a browser resolves.
    """
    match = _OPENALEX_ID.match(raw.strip())
    return match.group(1).upper() if match else None


def unescape(text: str) -> str:
    """Resolve HTML entities, repeatedly, until the text stops changing.

    Crossref serves ``Environmental Science &amp; Technology`` for a venue whose
    name contains an ampersand, and ``&amp;amp;`` for at least one that was
    escaped twice upstream. Left alone these reach ``refs.bib`` verbatim and
    typeset as the literal text ``&amp;``. The cap is what keeps a pathological
    record from looping; two rounds clear everything seen so far.
    """
    for _ in range(3):
        resolved = html.unescape(text)
        if resolved == text:
            return text
        text = resolved
    return text


def _strip_jats(text: str) -> str:
    """Crossref abstracts are JATS XML; flatten to plain text.

    Tags are stripped *before* entities are resolved, never after: abstracts
    write comparisons as ``p &lt; 0.05``, and unescaping first would turn
    ``[ p &lt; 0.05] but ... timestamp [ p &gt;`` into something the tag
    pattern happily eats as an element.
    """
    return unescape(" ".join(_JATS_TAG.sub("", text).split()))


def _crossref_names(item: dict[str, Any], key: str) -> tuple[str, ...]:
    """Crossref's ``author`` and ``editor`` carry the same shape."""
    names: list[str] = []
    for entry in item.get(key, []):
        given = str(entry.get("given", "")).strip()
        family = str(entry.get("family", "")).strip()
        name = f"{given} {family}".strip() or str(entry.get("name", "")).strip()
        if name:
            names.append(name)
    return tuple(names)


def _crossref_authors(item: dict[str, Any]) -> tuple[str, ...]:
    return _crossref_names(item, "author")


def _date_parts(item: dict[str, Any]) -> list[int] | None:
    """The first complete publication date Crossref offers, newest source first."""
    for key in ("published-print", "published-online", "issued", "created"):
        parts = item.get(key, {}).get("date-parts")
        if parts and parts[0] and parts[0][0] is not None:
            return [int(p) for p in parts[0] if p is not None]
    return None


def _crossref_year(item: dict[str, Any]) -> int | None:
    parts = _date_parts(item)
    return parts[0] if parts else None


def _crossref_month(item: dict[str, Any]) -> str | None:
    """The month as a bare number. BibTeX styles decide how to render it."""
    parts = _date_parts(item)
    return str(parts[1]) if parts and len(parts) > 1 else None


# Crossref's `updated-by` notice types, most severe first. `strongest` reports
# the first of these it finds, so the order is the policy.
RETRACTION_TYPES: Final[tuple[str, ...]] = (
    "retraction",
    "withdrawal",
    "removal",
    "partial_retraction",
    "expression_of_concern",
)
# Reported by `health`, but not a reason to stop citing the paper.
NOTICE_TYPES: Final[frozenset[str]] = frozenset(
    {"correction", "erratum", "addendum", "clarification"}
)


@dataclass(frozen=True, slots=True)
class Notice:
    """One `updated-by` entry: a later record that amends this one."""

    doi: str
    kind: str
    source: str
    date: str | None


def notices(message: Mapping[str, Any]) -> tuple[Notice, ...]:
    """Every amendment Crossref records against this work.

    Keyed off ``updated-by`` and never ``update-to``. Elsevier registers
    ``update-to`` on the retracted article as well as on the notice, so that
    field cannot tell which side of the pair a record is. ``updated-by`` is
    Crossref's own derived back-pointer and appears only on the amended work.
    """
    found: list[Notice] = []
    seen: set[tuple[str, str]] = set()
    for entry in message.get("updated-by", []) or []:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("type", "")).strip().lower()
        doi = str(entry.get("DOI", "")).strip().lower()
        if not kind or (doi, kind) in seen:
            continue
        seen.add((doi, kind))
        updated = entry.get("updated")
        date = None
        if isinstance(updated, dict):
            raw = updated.get("date-time")
            date = str(raw)[:10] if raw else None
        found.append(
            Notice(
                doi=doi,
                kind=kind,
                source=str(entry.get("source", "")).strip(),
                date=date,
            )
        )
    return tuple(found)


def is_notice(message: Mapping[str, Any]) -> bool:
    """Whether this record *is* a retraction notice rather than a retracted work.

    ``relation.retraction`` is the notice's own declaration about what it
    retracts, so it is a structural test and not a guess about the title.
    """
    relation = message.get("relation")
    return isinstance(relation, dict) and bool(relation.get("retraction"))


def strongest(found: Sequence[Notice]) -> str | None:
    """The most serious retraction-class notice, or ``None`` if there is none."""
    kinds = {notice.kind for notice in found}
    return next((kind for kind in RETRACTION_TYPES if kind in kinds), None)


def _first(value: Any) -> str | None:
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str) and value:
        return value
    return None


def _str(value: Any) -> str | None:
    """A scalar Crossref field as text, or ``None`` when it is absent or blank."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def fetch_crossref(doi: str, *, client: httpx.Client) -> dict[str, Any]:
    """Return the raw Crossref ``message`` object for ``doi``."""
    response = get_with_retry(f"{CROSSREF_API}/{doi}", client=client)
    if response.status_code == 404:
        raise SourceLookupError(f"Crossref has no record for DOI {doi!r}")
    response.raise_for_status()
    message = response.json().get("message")
    if not isinstance(message, dict):
        raise SourceLookupError(f"Unexpected Crossref response for DOI {doi!r}")
    return message


def fetch_openalex(doi: str, *, client: httpx.Client) -> dict[str, Any] | None:
    """Return the OpenAlex work for ``doi``, or ``None`` if it is not indexed."""
    response = get_with_retry(f"{OPENALEX_API}/doi:{doi}", client=client)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else None


def fetch_openalex_work(ident: str, *, client: httpx.Client) -> dict[str, Any]:
    """Return the OpenAlex work with this bare ``W123`` id."""
    response = get_with_retry(f"{OPENALEX_API}/{ident}", client=client)
    if response.status_code == 404:
        raise SourceLookupError(f"OpenAlex has no work {ident!r}")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise SourceLookupError(f"Unexpected OpenAlex response for {ident!r}")
    return payload


def _openalex_abstract(work: dict[str, Any]) -> str | None:
    """Reconstruct the abstract from OpenAlex's inverted index."""
    index = work.get("abstract_inverted_index")
    if not isinstance(index, dict):
        return None
    positions: list[tuple[int, str]] = [
        (pos, word)
        for word, spots in index.items()
        for pos in spots
        if isinstance(pos, int)
    ]
    if not positions:
        return None
    positions.sort()
    return unescape(" ".join(word for _, word in positions))


def build_paper(
    crossref: dict[str, Any], openalex: dict[str, Any] | None = None
) -> Paper:
    """Merge Crossref (authoritative) with OpenAlex (citations, OA status)."""
    title = _first(crossref.get("title")) or "Untitled"
    abstract = crossref.get("abstract")
    abstract_text = _strip_jats(str(abstract)) if abstract else None

    citations: int | None = None
    open_access: str | None = None
    pdf_url: str | None = None
    if openalex is not None:
        if abstract_text is None:
            abstract_text = _openalex_abstract(openalex)
        count = openalex.get("cited_by_count")
        if isinstance(count, int):
            citations = count
        oa = openalex.get("open_access")
        if isinstance(oa, dict):
            status = oa.get("oa_status")
            if isinstance(status, str):
                open_access = status.capitalize()
        best = openalex.get("best_oa_location")
        if isinstance(best, dict):
            candidate = best.get("pdf_url")
            if isinstance(candidate, str):
                pdf_url = candidate

    doi = crossref.get("DOI")
    venue = _first(crossref.get("container-title"))
    publisher = _str(crossref.get("publisher"))
    return Paper(
        title=unescape(" ".join(title.split())),
        authors=_crossref_authors(crossref),
        year=_crossref_year(crossref),
        doi=str(doi).lower() if doi else None,
        venue=unescape(venue) if venue else None,
        volume=_str(crossref.get("volume")),
        number=_str(crossref.get("issue")),
        pages=_str(crossref.get("page")),
        publisher=unescape(publisher) if publisher else None,
        editors=_crossref_names(crossref, "editor"),
        month=_crossref_month(crossref),
        abstract=abstract_text,
        url=str(crossref.get("URL")) if crossref.get("URL") else None,
        citations=citations,
        open_access=open_access,
        # Free: `fetch_crossref` already returns the whole message, so a paper
        # retracted before it was ever added is never filed as though it were not.
        retracted=None if is_notice(crossref) else strongest(notices(crossref)),
        pdf_url=pdf_url,
        entry_type=_ENTRY_TYPES.get(str(crossref.get("type", "")), "article"),
    )


def fetch_pdf(url: str, *, client: httpx.Client) -> bytes | None:
    """Download an open-access PDF, or ``None`` if it cannot be had.

    Publishers behind a bot check (ACM's DL among them) answer a scripted
    request with an HTML block page, sometimes under a 200, so the response is
    only trusted if it actually starts with the PDF magic bytes.
    """
    try:
        response = client.get(url, headers={"User-Agent": USER_AGENT}, timeout=60.0)
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    data = response.content
    return data if data.startswith(b"%PDF-") else None


_ENTRY_TYPES = {
    "journal-article": "article",
    "proceedings-article": "inproceedings",
    "book": "book",
    "book-chapter": "incollection",
    "dissertation": "phdthesis",
    "posted-content": "misc",
    "report": "techreport",
}

# OpenAlex uses its own vocabulary for the same distinction.
_OPENALEX_ENTRY_TYPES = {
    "article": "article",
    "conference-paper": "inproceedings",
    "proceedings-article": "inproceedings",
    "book": "book",
    "book-chapter": "incollection",
    "dissertation": "phdthesis",
    "preprint": "misc",
    "report": "techreport",
    "dataset": "misc",
    "other": "misc",
}


def _openalex_authors(work: dict[str, Any]) -> tuple[str, ...]:
    authors: list[str] = []
    for entry in work.get("authorships", []):
        if not isinstance(entry, dict):
            continue
        author = entry.get("author")
        name = author.get("display_name") if isinstance(author, dict) else None
        if isinstance(name, str) and name.strip():
            authors.append(name.strip())
    return tuple(authors)


def _openalex_venue(work: dict[str, Any]) -> str | None:
    location = work.get("primary_location")
    if not isinstance(location, dict):
        return None
    source = location.get("source")
    if not isinstance(source, dict):
        return None
    name = source.get("display_name")
    return unescape(str(name).strip()) or None if isinstance(name, str) else None


def _openalex_landing_page(work: dict[str, Any]) -> str | None:
    """The publisher's page for a work, used when it has no DOI to link to.

    USENIX and the other proceedings that mint no DOI are exactly the works
    that need this, so a note for one still records where it came from.
    """
    location = work.get("primary_location")
    if not isinstance(location, dict):
        return None
    landing = location.get("landing_page_url")
    return str(landing) if isinstance(landing, str) and landing.strip() else None


def paper_from_openalex(work: dict[str, Any]) -> Paper:
    """Build a :class:`Paper` from OpenAlex alone.

    The fallback for the handful of works Crossref 404s on but OpenAlex knows: a
    note with slightly scrappier metadata beats losing the paper entirely.
    """
    title = work.get("title") or work.get("display_name") or "Untitled"
    doi = work.get("doi")
    year = work.get("publication_year")

    open_access: str | None = None
    oa = work.get("open_access")
    if isinstance(oa, dict) and isinstance(oa.get("oa_status"), str):
        open_access = str(oa["oa_status"]).capitalize()

    pdf_url: str | None = None
    best = work.get("best_oa_location")
    if isinstance(best, dict) and isinstance(best.get("pdf_url"), str):
        pdf_url = str(best["pdf_url"])

    # OpenAlex keeps the page range split, and is the only source for a work
    # Crossref has never heard of.
    biblio = work.get("biblio")
    volume = number = pages = None
    if isinstance(biblio, dict):
        volume = _str(biblio.get("volume"))
        number = _str(biblio.get("issue"))
        first, last = _str(biblio.get("first_page")), _str(biblio.get("last_page"))
        pages = f"{first}--{last}" if first and last else first

    normalized_doi = (
        str(doi).strip().removeprefix("https://doi.org/").lower()
        if isinstance(doi, str) and doi.strip()
        else None
    )
    return Paper(
        title=unescape(" ".join(str(title).split())),
        authors=_openalex_authors(work),
        year=int(year) if isinstance(year, int) else None,
        doi=normalized_doi,
        venue=_openalex_venue(work),
        volume=volume,
        number=number,
        pages=pages,
        abstract=_openalex_abstract(work),
        url=(
            f"https://doi.org/{normalized_doi}"
            if normalized_doi
            else _openalex_landing_page(work)
        ),
        citations=(
            work["cited_by_count"]
            if isinstance(work.get("cited_by_count"), int)
            else None
        ),
        open_access=open_access,
        pdf_url=pdf_url,
        entry_type=_OPENALEX_ENTRY_TYPES.get(str(work.get("type", "")), "article"),
    )


def resolve(raw_doi: str, *, client: httpx.Client | None = None) -> Paper:
    """Look up ``raw_doi`` in Crossref, enriched with OpenAlex where available."""
    doi = normalize_doi(raw_doi)
    owns_client = client is None
    active = client or httpx.Client(follow_redirects=True)
    try:
        crossref = fetch_crossref(doi, client=active)
        try:
            openalex = fetch_openalex(doi, client=active)
        except httpx.HTTPError:
            openalex = None  # OpenAlex is enrichment only; never fail the lookup
        return build_paper(crossref, openalex)
    finally:
        if owns_client:
            active.close()


def resolve_source(
    raw: str, *, client: httpx.Client | None = None
) -> tuple[Paper, str | None]:
    """Resolve a DOI or an OpenAlex id. Returns ``(paper, openalex_id)``.

    A DOI still goes to Crossref first, because Crossref is authoritative for
    the fields the bibliography needs. An OpenAlex id is the way in for the
    works Crossref cannot know about. USENIX proceedings mint no DOI, so
    ``W2300484078`` is the only identifier Passive Wi-Fi has.
    """
    ident = as_openalex_id(raw)
    owns_client = client is None
    active = client or httpx.Client(follow_redirects=True)
    try:
        if ident is not None:
            return paper_from_openalex(fetch_openalex_work(ident, client=active)), ident

        # The same two calls `resolve` makes, kept here so the OpenAlex id the
        # enrichment already carries is returned rather than thrown away and
        # re-fetched by `relink`'s backfill.
        doi = normalize_doi(raw)
        crossref = fetch_crossref(doi, client=active)
        try:
            openalex = fetch_openalex(doi, client=active)
        except httpx.HTTPError:
            openalex = None  # OpenAlex is enrichment only; never fail the lookup
        found = as_openalex_id(str(openalex.get("id", ""))) if openalex else None
        return build_paper(crossref, openalex), found
    finally:
        if owns_client:
            active.close()
