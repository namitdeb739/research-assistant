"""Minimal Notion REST client for the “Research Resources” database.

Only the two operations this repo needs: append a paper, and read every paper
back out. Judgement fields (Relevance, Topics, Section, Key Takeaway, Rating)
are never written — they are filled in by hand in Notion.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from earth_computers.refs.models import Paper

if TYPE_CHECKING:
    import httpx

API_ROOT = "https://api.notion.com/v1"
API_VERSION = "2022-06-28"

# "Research Resources" database in the B.Comp. Dissertation page.
DEFAULT_DATABASE_ID = "8b19af5a-f122-4e54-9c36-1e2d4bc3cc19"

# Notion rejects rich_text values longer than this.
_TEXT_LIMIT = 2000


class NotionError(Exception):
    """Raised when the Notion API rejects a request or is not configured."""


def _token() -> str:
    token = os.getenv("NOTION_TOKEN")
    if not token:
        raise NotionError(
            "NOTION_TOKEN is not set. Create an internal integration at "
            "https://www.notion.so/my-integrations, share the Research Resources "
            "database with it, and put the secret in .env"
        )
    return token


def _database_id() -> str:
    return os.getenv("NOTION_DATABASE_ID", DEFAULT_DATABASE_ID)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_token()}",
        "Notion-Version": API_VERSION,
        "Content-Type": "application/json",
    }


def _rich_text(value: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": {"content": value[:_TEXT_LIMIT]}}]


def _plain(prop: dict[str, Any] | None) -> str | None:
    """Flatten a title/rich_text property to plain text."""
    if not prop:
        return None
    for key in ("title", "rich_text"):
        parts = prop.get(key)
        if isinstance(parts, list):
            text = "".join(str(part.get("plain_text", "")) for part in parts)
            return text or None
    return None


def paper_properties(paper: Paper) -> dict[str, Any]:
    """Map a :class:`Paper` onto Research Resources properties."""
    props: dict[str, Any] = {
        "Name": {"title": _rich_text(paper.title)},
        "Status": {"select": {"name": "Inbox"}},
    }
    if paper.authors:
        props["Author String"] = {"rich_text": _rich_text(", ".join(paper.authors))}
    if paper.year is not None:
        props["Year"] = {"number": paper.year}
    if paper.doi:
        props["DOI"] = {"rich_text": _rich_text(paper.doi)}
    if paper.abstract:
        props["Abstract"] = {"rich_text": _rich_text(paper.abstract)}
    if paper.url:
        props["URL"] = {"url": paper.url}
    if paper.citations is not None:
        props["Citations"] = {"number": paper.citations}
    if paper.open_access:
        props["Open Access"] = {"select": {"name": paper.open_access}}
    return props


def find_by_doi(doi: str, *, client: httpx.Client) -> str | None:
    """Return the page id of an existing row with this DOI, if any."""
    response = client.post(
        f"{API_ROOT}/databases/{_database_id()}/query",
        headers=_headers(),
        json={"filter": {"property": "DOI", "rich_text": {"equals": doi}}},
        timeout=30.0,
    )
    response.raise_for_status()
    results = response.json().get("results", [])
    if results:
        return str(results[0]["id"])
    return None


def create_paper(paper: Paper, *, client: httpx.Client) -> str:
    """Create a Research Resources row. Returns the new page URL."""
    response = client.post(
        f"{API_ROOT}/pages",
        headers=_headers(),
        json={
            "parent": {"database_id": _database_id()},
            "properties": paper_properties(paper),
        },
        timeout=30.0,
    )
    if response.status_code >= 400:
        raise NotionError(f"Notion rejected the page: {response.text}")
    return str(response.json().get("url", ""))


def _row_to_paper(row: dict[str, Any]) -> tuple[Paper, str] | None:
    props = row.get("properties", {})
    title = _plain(props.get("Name"))
    if not title:
        return None

    authors = _plain(props.get("Author String")) or ""
    year_prop = props.get("Year", {}).get("number")
    venue_prop = props.get("Venue", {}).get("select")
    doi = _plain(props.get("DOI"))

    paper = Paper(
        title=title,
        authors=tuple(a.strip() for a in authors.split(",") if a.strip()),
        year=int(year_prop) if isinstance(year_prop, int | float) else None,
        doi=doi,
        venue=venue_prop.get("name") if isinstance(venue_prop, dict) else None,
        url=props.get("URL", {}).get("url"),
    )

    # Prefer Notion's own Cite Key formula so citations match what you see there.
    formula = props.get("Cite Key", {}).get("formula", {})
    key = formula.get("string") if isinstance(formula, dict) else None
    return paper, (key or paper.cite_key())


def fetch_all(*, client: httpx.Client) -> list[tuple[Paper, str]]:
    """Read every row of the database, following pagination."""
    entries: list[tuple[Paper, str]] = []
    payload: dict[str, Any] = {"page_size": 100}
    while True:
        response = client.post(
            f"{API_ROOT}/databases/{_database_id()}/query",
            headers=_headers(),
            json=payload,
            timeout=30.0,
        )
        if response.status_code >= 400:
            raise NotionError(f"Notion query failed: {response.text}")
        body = response.json()
        for row in body.get("results", []):
            entry = _row_to_paper(row)
            if entry is not None:
                entries.append(entry)
        if not body.get("has_more"):
            return entries
        payload["start_cursor"] = body["next_cursor"]
