"""Entry point for ``python -m research_assistant``.

Keeps callers off the internal module layout: the CLI is reachable by the
package name alone, so becoming a console script later changes nothing here.
"""

from __future__ import annotations

from research_assistant.cli import app

app()
