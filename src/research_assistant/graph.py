"""Citation-graph traversal over OpenAlex.

Given the papers already in the vault, this finds the ones around them: what
they cite, what cites them, and what OpenAlex considers related. Purely a
discovery layer: it deals in OpenAlex work IDs and hands them to
:mod:`research_assistant.sources` for the authoritative Crossref record.

Roots are every note *without* the ``harvested`` tag. That keeps a re-run
idempotent: the papers a previous run added do not themselves become roots, so
the graph never quietly walks out to depth 2. To push further, drop the
``harvested`` tag from the paper worth expanding and run again.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    import httpx

OPENALEX_API = "https://api.openalex.org/works"

# Crossref and OpenAlex both ask for a contact address for the polite pool. It
# is the caller's to supply, so it comes from the environment: see the same
# variable in :mod:`research_assistant.sources`.
_USER_AGENT = os.getenv("RESEARCH_ASSISTANT_USER_AGENT", "research-assistant/0.1")

# OpenAlex accepts an OR-joined filter of up to 50 ids per request.
OPENALEX_MAX_IDS = 50

_ID_PREFIX = "https://openalex.org/"
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# How a candidate was found. A work can arrive by more than one route.
REFERENCE = "reference"
CITATION = "citation"
RELATED = "related"

# Everything the note writer and the linker need, in one round trip.
WORK_FIELDS = (
    "id,doi,title,publication_year,type,cited_by_count,open_access,"
    "best_oa_location,topics,primary_location,abstract_inverted_index,"
    "referenced_works,related_works"
)


class GraphError(Exception):
    """Raised when OpenAlex cannot be reached or answers unusably."""


@dataclass(frozen=True, slots=True)
class Candidate:
    """A work discovered next to the vault, not yet in it."""

    openalex_id: str
    doi: str | None
    provenance: frozenset[str]
    seeds: frozenset[str]

    def merge(self, other: Candidate) -> Candidate:
        """Union two sightings of the same work found by different routes."""
        return Candidate(
            openalex_id=self.openalex_id,
            doi=self.doi or other.doi,
            provenance=self.provenance | other.provenance,
            seeds=self.seeds | other.seeds,
        )


def bare_id(value: str) -> str:
    """``https://openalex.org/W123`` or ``W123`` -> ``W123``."""
    return value.removeprefix(_ID_PREFIX).strip()


def slug(value: str) -> str:
    """``Environmental Engineering`` -> ``environmental-engineering``."""
    return _NON_ALNUM.sub("-", value.lower()).strip("-")


def chunked(values: Sequence[str], size: int = OPENALEX_MAX_IDS) -> Iterator[list[str]]:
    """Split ``values`` into runs of at most ``size``."""
    for start in range(0, len(values), size):
        yield list(values[start : start + size])


def _get(params: dict[str, str], *, client: httpx.Client) -> dict[str, Any]:
    response = client.get(
        OPENALEX_API,
        params=params,
        headers={"User-Agent": _USER_AGENT},
        timeout=60.0,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise GraphError("Unexpected OpenAlex response: not an object")
    return payload


def fetch_works(
    ids: Sequence[str], *, client: httpx.Client, select: str = WORK_FIELDS
) -> list[dict[str, Any]]:
    """Fetch full work records for ``ids``, batched 50 to a request.

    Ids OpenAlex has merged or deleted simply do not come back; the caller sees
    a shorter list rather than an error.
    """
    works: list[dict[str, Any]] = []
    for batch in chunked([bare_id(value) for value in ids]):
        payload = _get(
            {
                "filter": f"openalex_id:{'|'.join(batch)}",
                "per-page": "100",
                "select": select,
            },
            client=client,
        )
        results = payload.get("results")
        if isinstance(results, list):
            works.extend(item for item in results if isinstance(item, dict))
    return works


def fetch_by_doi(dois: Sequence[str], *, client: httpx.Client) -> list[dict[str, Any]]:
    """Fetch full work records for ``dois``, batched 50 to a request."""
    works: list[dict[str, Any]] = []
    for batch in chunked(list(dois)):
        payload = _get(
            {
                "filter": f"doi:{'|'.join(batch)}",
                "per-page": "100",
                "select": WORK_FIELDS,
            },
            client=client,
        )
        results = payload.get("results")
        if isinstance(results, list):
            works.extend(item for item in results if isinstance(item, dict))
    return works


def citations_of(
    seed_ids: Sequence[str], *, client: httpx.Client
) -> list[dict[str, Any]]:
    """Fetch the works citing any of ``seed_ids``, following the cursor."""
    works: list[dict[str, Any]] = []
    for batch in chunked([bare_id(value) for value in seed_ids]):
        cursor: str | None = "*"
        while cursor:
            payload = _get(
                {
                    "filter": f"cites:{'|'.join(batch)}",
                    "per-page": "200",
                    "cursor": cursor,
                    "select": WORK_FIELDS,
                },
                client=client,
            )
            results = payload.get("results")
            if isinstance(results, list):
                works.extend(item for item in results if isinstance(item, dict))
            meta = payload.get("meta")
            next_cursor = meta.get("next_cursor") if isinstance(meta, dict) else None
            # An empty page ends the walk even if OpenAlex still offers a cursor.
            cursor = next_cursor if isinstance(next_cursor, str) and results else None
    return works


def _linked_ids(work: dict[str, Any], key: str) -> list[str]:
    raw = work.get(key)
    return (
        [bare_id(str(item)) for item in raw if isinstance(item, str)]
        if isinstance(raw, list)
        else []
    )


def _doi_of(work: dict[str, Any]) -> str | None:
    doi = work.get("doi")
    if not isinstance(doi, str) or not doi.strip():
        return None
    return doi.strip().removeprefix("https://doi.org/").lower()


def topics_of(work: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return ``(topic names, subfield slugs)`` for a work.

    OpenAlex assigns these itself, so the grouping is deterministic and carries
    no judgement, the same distinction the vault draws between ``topics`` and
    the prose sections of a note.
    """
    raw = work.get("topics")
    if not isinstance(raw, list):
        return (), ()
    names: list[str] = []
    subfields: list[str] = []
    for topic in raw:
        if not isinstance(topic, dict):
            continue
        name = topic.get("display_name")
        if isinstance(name, str) and name.strip() and name not in names:
            names.append(name.strip())
        subfield = topic.get("subfield")
        if isinstance(subfield, dict):
            label = subfield.get("display_name")
            if isinstance(label, str) and label.strip():
                candidate = slug(label)
                if candidate and candidate not in subfields:
                    subfields.append(candidate)
    return tuple(names), tuple(subfields)


def harvest(
    seed_works: Iterable[dict[str, Any]],
    *,
    client: httpx.Client,
    backward: bool = True,
    forward: bool = True,
    related: bool = True,
) -> dict[str, Candidate]:
    """Walk one hop out from ``seed_works``. Returns candidates by OpenAlex id.

    The seeds themselves are never returned, even when they cite each other.
    """
    seeds = list(seed_works)
    seed_ids = {bare_id(str(work.get("id", ""))) for work in seeds}
    seed_ids.discard("")

    found: dict[str, Candidate] = {}

    def add(work_id: str, doi: str | None, source: str, seed_key: str) -> None:
        ident = bare_id(work_id)
        if not ident or ident in seed_ids:
            return
        candidate = Candidate(
            openalex_id=ident,
            doi=doi,
            provenance=frozenset({source}),
            seeds=frozenset({seed_key} if seed_key else set()),
        )
        existing = found.get(ident)
        found[ident] = existing.merge(candidate) if existing else candidate

    for work in seeds:
        seed_key = bare_id(str(work.get("id", "")))
        if backward:
            for ident in _linked_ids(work, "referenced_works"):
                add(ident, None, REFERENCE, seed_key)
        if related:
            for ident in _linked_ids(work, "related_works"):
                add(ident, None, RELATED, seed_key)

    if forward and seed_ids:
        for work in citations_of(sorted(seed_ids), client=client):
            cited = set(_linked_ids(work, "referenced_works")) & seed_ids
            ident = str(work.get("id", ""))
            for seed_key in sorted(cited) or [""]:
                add(ident, _doi_of(work), CITATION, seed_key)

    return found
