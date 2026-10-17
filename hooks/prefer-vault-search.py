#!/usr/bin/env python3
"""
PreToolUse hook that nudges vault reads toward `find` / `show`.

A paper note is a couple of kilobytes and a vault is hundreds of them. Reading
three and generalising is a plausible-sounding answer built on whichever notes
happened to get opened, and a `rg` over the folder returns unranked substring
hits with no idea which one matters. `research-assistant find` ranks by BM25
over title, topics, abstract and notes; `show` resolves one note by cite key,
name or DOI.

Warn-only: always exits 0. Both tools still run, because the exceptions are
real: inspecting one note's raw YAML, or debugging what `tidy` did to a body,
are things the search commands genuinely cannot do.

`pdfs/` is deliberately NOT covered. `research-assistant pdf <key>` prints a
path precisely so it can be handed to Read, and warning about that would fight
the workflow.

Silent unless the project names a vault, in `.research-assistant.toml` or
`VAULT_PAPERS_DIR`. There is no default: without a vault to point at, every path
this could match belongs to somebody else.

At most two nudges per session: one for searching, one for reading.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11, which bare `python3` may well be.
    tomllib = None

# Which argument carries the path, per tool.
PATH_KEYS = {
    "Read": "file_path",
    "Grep": "path",
    "Glob": "path",
}

# A shell command naming the folder bypasses Read and Grep, and so this hook,
# entirely. Only the retrieval commands count: `find` names no path, and a
# one-off reconciliation in Python is a legitimate reason to be in Bash.
SHELL_READERS = re.compile(
    r"\b(rg|grep|ag|cat|bat|head|tail|less|ls|eza|find|fd|wc|awk|sed)\b"
)

SEARCH_NUDGE = (
    'Prefer `research-assistant find "<terms>"` over searching the papers folder.\n'
    "   It ranks by BM25 over title, topics, abstract and notes, prints the\n"
    "   cite key and a marked snippet, and takes --topic/--tag/--has-pdf.\n"
    "   A grep here returns unranked substring hits across the whole corpus."
)

READ_NUDGE = (
    "Prefer `research-assistant show <cite_key>` over reading notes by path.\n"
    "   It resolves by cite key, note name or DOI, with no 90-character filename\n"
    "   to reproduce, and `find` searches the whole corpus at once. Reading a\n"
    "   handful and generalising is the failure these commands exist to replace."
)


def recorded_papers_dir(text: str) -> object:
    """The config's ``papers_dir`` value, parsed or not."""
    if tomllib is not None:
        return tomllib.loads(text).get("papers_dir")
    # Without tomllib, read the one key this needs: a top-level quoted string,
    # which is the shape the README documents.
    match = re.search(r"""^\s*papers_dir\s*=\s*(["'])(.*?)\1""", text, re.MULTILINE)
    return match.group(2) if match else None


def papers_dir() -> Path | None:
    """The vault folder, or ``None`` when this project has no vault.

    The same resolution the CLI does, minus the flag it has no access to. Kept
    inline rather than imported: a hook runs under whatever interpreter Claude
    Code has, which need not be the one the package is installed into.
    """
    override = os.environ.get("VAULT_PAPERS_DIR")
    if override:
        return Path(override).expanduser()
    here = Path.cwd()
    for folder in (here, *here.parents):
        found = folder / ".research-assistant.toml"
        if not found.is_file():
            continue
        # TOMLDecodeError is a ValueError.
        with contextlib.suppress(OSError, UnicodeDecodeError, ValueError):
            recorded = recorded_papers_dir(found.read_text(encoding="utf-8"))
            if isinstance(recorded, str):
                return found.parent / Path(recorded).expanduser()
        return None
    return None


def watched_roots(papers: Path) -> list[Path]:
    """The prose folders. Not ``pdfs/``, which is meant to be read directly."""
    return [papers, papers.parent / "topics"]


def inside(target: str, roots: list[Path]) -> bool:
    try:
        path = Path(target).expanduser().resolve()
    except (OSError, ValueError, RuntimeError):
        return False
    return any(path == root or root in path.parents for root in roots)


def session_file(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", session_id) or "default"
    return Path(tempfile.gettempdir()) / f"claude_vault_search_hook_{safe}.json"


def load_warned(state_path: Path) -> set[str]:
    if state_path.exists():
        try:
            return set(json.loads(state_path.read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    return set()


def save_warned(state_path: Path, warned: set[str]) -> None:
    # A tempdir that will not take the file costs a repeated nudge, nothing more.
    with contextlib.suppress(OSError):
        state_path.write_text(json.dumps(sorted(warned)))


def main() -> int:
    folder = papers_dir()
    if folder is None:
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0

    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})

    if tool == "Bash":
        command = str(tool_input.get("command", ""))
        # Match on the folder's own name as well as the full path: the vault
        # lives behind a `$V` or a `cd`, and the basename is distinctive enough.
        named = (
            str(folder) in command or f"{folder.parent.name}/{folder.name}" in command
        )
        if not (named and SHELL_READERS.search(command)):
            return 0
        kind, nudge = "search", SEARCH_NUDGE
    else:
        key = PATH_KEYS.get(tool)
        if key is None:
            return 0
        target = tool_input.get(key) or ""
        if not target or not inside(str(target), watched_roots(folder)):
            return 0
        kind, nudge = (
            ("read", READ_NUDGE) if tool == "Read" else ("search", SEARCH_NUDGE)
        )

    state_path = session_file(payload.get("session_id", ""))
    warned = load_warned(state_path)
    if kind in warned:
        return 0
    warned.add(kind)
    save_warned(state_path, warned)

    print(f"\n💡 {nudge}", file=sys.stderr)
    print(f"   This is a nudge, not a block; the {tool} still ran.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
