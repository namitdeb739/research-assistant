"""Deterministic metadata lookup from Crossref and OpenAlex.

No model in the loop: given a DOI, the same record comes back every time.
Judgement fields (Relevance, Topics, Section, Key Takeaway, Rating) are left
empty on purpose — those are yours to fill in Notion.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from earth_computers.refs.models import Paper

CROSSREF_API = "https://api.crossref.org/works"
OPENALEX_API = "https://api.openalex.org/works"

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


def fetch_crossref(doi: str, *, client: httpx.Client) -> dict[str, Any]:
    """Return the raw Crossref ``message`` object for ``doi``."""
    response = client.get(
        f"{CROSSREF_API}/{doi}", headers={"User-Agent": _USER_AGENT}, timeout=30.0
    )
    if response.status_code == 404:
        raise DoiLookupError(f"Crossref has no record for DOI {doi!r}")
    response.raise_for_status()
    message = response.json().get("message")
    if not isinstance(message, dict):
        raise DoiLookupError(f"Unexpected Crossref response for DOI {doi!r}")
    return message


def fetch_openalex(doi: str, *, client: httpx.Client) -> dict[str, Any] | None:
    """Return the OpenAlex work for ``doi``, or ``None`` if it is not indexed."""
    response = client.get(
        f"{OPENALEX_API}/doi:{doi}", headers={"User-Agent": _USER_AGENT}, timeout=30.0
    )
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
        entry_type=_ENTRY_TYPES.get(str(crossref.get("type", "")), "article"),
    )


_ENTRY_TYPES = {
    "journal-article": "article",
    "proceedings-article": "inproceedings",
    "book": "book",
    "book-chapter": "incollection",
    "dissertation": "phdthesis",
    "posted-content": "misc",
    "report": "techreport",
}


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
