"""Deterministic metadata lookup from Crossref and OpenAlex.

No model in the loop: given a DOI, the same record comes back every time.
Judgement fields (Relevance, Topics, Section, Key Takeaway, Rating) are left
empty on purpose — those are yours to fill in Obsidian.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

import httpx

from earth_computers.refs.models import Paper

if TYPE_CHECKING:
    from collections.abc import Callable

CROSSREF_API = "https://api.crossref.org/works"
OPENALEX_API = "https://api.openalex.org/works"

# Harvesting the citation graph makes hundreds of sequential calls where adding
# one paper made two. A single transient 503 would otherwise abort the run.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = 1.0

# Crossref asks for a contact address in the User-Agent for the polite pool.
_USER_AGENT = "earth-computers/0.1 (mailto:namitdeb739@gmail.com)"

_JATS_TAG = re.compile(r"<[^>]+>")
_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", re.IGNORECASE)


class DoiLookupError(Exception):
    """Raised when a DOI cannot be resolved."""


def normalize_doi(raw: str) -> str:
    """Strip any ``https://doi.org/`` or ``doi:`` prefix and lowercase."""
    return _DOI_PREFIX.sub("", raw.strip()).strip("/").lower()


def _strip_jats(text: str) -> str:
    """Crossref abstracts are JATS XML; flatten to plain text."""
    return " ".join(_JATS_TAG.sub("", text).split())


def _crossref_authors(item: dict[str, Any]) -> tuple[str, ...]:
    authors: list[str] = []
    for entry in item.get("author", []):
        given = str(entry.get("given", "")).strip()
        family = str(entry.get("family", "")).strip()
        name = f"{given} {family}".strip() or str(entry.get("name", "")).strip()
        if name:
            authors.append(name)
    return tuple(authors)


def _crossref_year(item: dict[str, Any]) -> int | None:
    for key in ("published-print", "published-online", "issued", "created"):
        parts = item.get(key, {}).get("date-parts")
        if parts and parts[0] and parts[0][0] is not None:
            return int(parts[0][0])
    return None


def _first(value: Any) -> str | None:
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str) and value:
        return value
    return None


def _retry_after(response: httpx.Response, attempt: int) -> float:
    """Seconds to wait before retrying, honouring ``Retry-After`` if sent."""
    header = response.headers.get("Retry-After")
    if header:
        try:
            seconds: float = float(str(header))
        except ValueError:
            pass
        else:
            return seconds if seconds > 0.0 else 0.0
    return BACKOFF_SECONDS * (2.0**attempt)


def get_with_retry(
    url: str,
    *,
    client: httpx.Client,
    timeout: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    """GET ``url``, retrying on rate limits and transient server errors.

    Returns the last response either way — the caller still decides what a 404
    or a 500 means, so nothing is swallowed here.
    """
    response = client.get(url, headers={"User-Agent": _USER_AGENT}, timeout=timeout)
    for attempt in range(MAX_ATTEMPTS - 1):
        if response.status_code not in RETRY_STATUSES:
            return response
        sleep(_retry_after(response, attempt))
        response = client.get(url, headers={"User-Agent": _USER_AGENT}, timeout=timeout)
    return response


def fetch_crossref(doi: str, *, client: httpx.Client) -> dict[str, Any]:
    """Return the raw Crossref ``message`` object for ``doi``."""
    response = get_with_retry(f"{CROSSREF_API}/{doi}", client=client)
    if response.status_code == 404:
        raise DoiLookupError(f"Crossref has no record for DOI {doi!r}")
    response.raise_for_status()
    message = response.json().get("message")
    if not isinstance(message, dict):
        raise DoiLookupError(f"Unexpected Crossref response for DOI {doi!r}")
    return message


def fetch_openalex(doi: str, *, client: httpx.Client) -> dict[str, Any] | None:
    """Return the OpenAlex work for ``doi``, or ``None`` if it is not indexed."""
    response = get_with_retry(f"{OPENALEX_API}/doi:{doi}", client=client)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else None


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
    return " ".join(word for _, word in positions)


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
    return Paper(
        title=" ".join(title.split()),
        authors=_crossref_authors(crossref),
        year=_crossref_year(crossref),
        doi=str(doi).lower() if doi else None,
        venue=_first(crossref.get("container-title")),
        abstract=abstract_text,
        url=str(crossref.get("URL")) if crossref.get("URL") else None,
        citations=citations,
        open_access=open_access,
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
        response = client.get(url, headers={"User-Agent": _USER_AGENT}, timeout=60.0)
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
    return str(name).strip() or None if isinstance(name, str) else None


def paper_from_openalex(work: dict[str, Any]) -> Paper:
    """Build a :class:`Paper` from OpenAlex alone.

    The fallback for the handful of works Crossref 404s on but OpenAlex knows —
    a note with slightly scrappier metadata beats losing the paper entirely.
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

    normalized_doi = (
        str(doi).strip().removeprefix("https://doi.org/").lower()
        if isinstance(doi, str) and doi.strip()
        else None
    )
    return Paper(
        title=" ".join(str(title).split()),
        authors=_openalex_authors(work),
        year=int(year) if isinstance(year, int) else None,
        doi=normalized_doi,
        venue=_openalex_venue(work),
        abstract=_openalex_abstract(work),
        url=f"https://doi.org/{normalized_doi}" if normalized_doi else None,
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
