"""Entry point for ``python -m vaultref``.

Keeps callers off the internal module layout: the CLI is reachable by the
package name alone, so becoming a console script later changes nothing here.
"""

from __future__ import annotations

from vaultref.cli import app

app()
