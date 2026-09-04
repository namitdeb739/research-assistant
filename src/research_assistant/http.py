"""One HTTP policy for both indexes: the polite pool and the retry.

Crossref and OpenAlex are reached from two modules, and the retry only ever
covered one of them. Harvesting the citation graph makes hundreds of sequential
calls where adding one paper made two, so the traffic that most needed the retry
was the traffic without it.

Nothing here knows what a work is. It sends a GET and hands back the response.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import httpx

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = 1.0

# Crossref asks for a contact address in the User-Agent for the polite pool. The
# address must be the caller's own, so it comes from the environment rather than
# being baked in: a shared literal would make everyone's traffic look like one
# user's. Unset means no mailto, which is merely the anonymous pool, not an error.
USER_AGENT = os.getenv("RESEARCH_ASSISTANT_USER_AGENT", "research-assistant/0.1")


def retry_after(response: httpx.Response, attempt: int) -> float:
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
    params: Mapping[str, str] | None = None,
    timeout: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    """GET ``url``, retrying on rate limits and transient server errors.

    Returns the last response either way, because the caller still decides what
    a 404 or a 500 means and nothing is swallowed here.
    """

    def send() -> httpx.Response:
        return client.get(
            url,
            params=dict(params) if params is not None else None,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        )

    response = send()
    for attempt in range(MAX_ATTEMPTS - 1):
        if response.status_code not in RETRY_STATUSES:
            return response
        sleep(retry_after(response, attempt))
        response = send()
    return response
