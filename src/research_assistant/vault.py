"""Obsidian vault backend for the “Research Resources” database.

One Markdown note per resource, named ``<cite_key>.md``, with the bibliographic
record in YAML frontmatter. The filename is the uniqueness guard.

The frontmatter holds only *intrinsic* properties — facts about the resource
itself. Judgements about it (is it any good, does it belong in related work) and
progress through it (read yet?) are prose in the note body, not properties: a
five-point scale in a table is a worse record of an opinion than a sentence is.

A note is written once and never rewritten: Obsidian owns it afterwards, and its
property editor reformats frontmatter freely. Reading tolerates that; writing
does not fight it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import yaml

from earth_computers.refs.models import Paper

if TYPE_CHECKING:
    from pathlib import Path

# PDFs sit beside the notes, in a sibling of the papers folder. Obsidian
# resolves ``![[<key>.pdf]]`` by filename, so the exact location only has to be
# somewhere in the vault. They keep the cite key as their name: a PDF is never
# read as prose, and the key avoids the punctuation a title drags in.
PDFS_DIRNAME = "pdfs"

# Obsidian rejects these in file names; # ^ [ ] additionally mean something in
# link syntax and would break a ``[[wikilink]]`` to the note. Slashes become a
# dash rather than vanishing — deleting one turns "TCP/IP" into "TCPIP".
_SEPARATORS = re.compile(r"\s*[\\/]\s*")
_FORBIDDEN = re.compile(r'[:*?"<>|#^\[\]]')

_FENCE = "---"
_PDF_MAGIC = b"%PDF-"


class VaultError(Exception):
    """Raised when the vault is missing, malformed, or would be clobbered."""


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a note into its frontmatter mapping and its body."""
    if not text.startswith(_FENCE):
        return {}, text
    parts = text.split(f"\n{_FENCE}", 2)
    if len(parts) < 2:
        return {}, text
    loaded = yaml.safe_load(parts[0][len(_FENCE) :])
    body = parts[1].lstrip("\n")
    return (loaded if isinstance(loaded, dict) else {}), body


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def _str(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def note_name(title: str, key: str) -> str:
    """What the note is called in the vault: the title, made filename-safe.

    The verbatim title stays in the ``title`` property, which is what BibTeX
    reads, so sanitising here costs nothing bibliographically. A colon becomes
    a dash rather than vanishing, since it usually separates a title from its
    subtitle and reads wrong when simply deleted.
    """
    name = _SEPARATORS.sub("-", title.replace(": ", " - ").replace(":", " - "))
    cleaned = " ".join(_FORBIDDEN.sub("", name).split()).strip(". -")
    # A title made only of punctuation would leave a meaningless file name.
    return cleaned if any(char.isalnum() for char in cleaned) else key


def note_text(paper: Paper, key: str, *, pdf_name: str | None = None) -> str:
    """Render the full Markdown note for ``paper``.

    The abstract goes in the body, not the frontmatter: Obsidian rewrites
    multi-line properties on edit, and it is 2000 characters of noise in a table.
    ``pdf_name`` embeds the saved PDF; without one the note still records
    ``pdf_url`` so the paper can be fetched by hand later.
    """
    front: dict[str, Any] = {
        "title": paper.title,
        "cite_key": key,
        "entry_type": paper.entry_type,
        "authors": list(paper.authors),
        "year": paper.year,
        "venue": paper.venue,
        "doi": paper.doi,
        "url": paper.url,
        "pdf": f"[[{pdf_name}]]" if pdf_name else None,
        "pdf_url": paper.pdf_url,
        "code_url": None,
        "citations": paper.citations,
        "open_access": paper.open_access.lower() if paper.open_access else None,
        "topics": [],
        "cites": [],
        "tags": ["paper"],
    }
    dumped = yaml.safe_dump(front, sort_keys=False, allow_unicode=True, width=10_000)
    abstract = paper.abstract or ""
    # The embed goes last: a rendered PDF is tall, and the notes matter more.
    embed = f"\n## PDF\n\n![[{pdf_name}]]\n" if pdf_name else ""
    return (
        f"{_FENCE}\n{dumped}{_FENCE}\n\n"
        f"## Key takeaway\n\n\n"
        f"## Abstract\n\n{abstract}\n\n"
        f"## Notes\n"
        f"{embed}"
    )


def save_pdf(key: str, data: bytes, *, pdfs_dir: Path) -> Path:
    """Write ``<key>.pdf`` into the vault. Returns the new file path."""
    if not data.startswith(_PDF_MAGIC):
        raise VaultError(
            f"{key}: the download is not a PDF — publishers behind a bot check "
            "serve an HTML block page with a 200. Save it from the browser instead."
        )
    pdfs_dir.mkdir(parents=True, exist_ok=True)
    path = pdfs_dir / f"{key}.pdf"
    path.write_bytes(data)
    return path


def find_by_doi(doi: str, *, papers_dir: Path) -> Path | None:
    """Return the note holding this DOI, if any."""
    if not papers_dir.is_dir():
        return None
    wanted = doi.strip().lower()
    for path in sorted(papers_dir.glob("*.md")):
        front, _ = _split_frontmatter(path.read_text(encoding="utf-8"))
        if _str(front.get("doi")) and str(front["doi"]).strip().lower() == wanted:
            return path
    return None


def create_paper(
    paper: Paper, key: str, *, papers_dir: Path, pdf_name: str | None = None
) -> Path:
    """Write the note into the vault, named for its title. Returns its path."""
    papers_dir.mkdir(parents=True, exist_ok=True)
    path = papers_dir / f"{note_name(paper.title, key)}.md"
    if path.exists():
        raise VaultError(f"{path} already exists — refusing to overwrite it")
    path.write_text(note_text(paper, key, pdf_name=pdf_name), encoding="utf-8")
    return path


def _note_to_paper(
    front: dict[str, Any], fallback_key: str
) -> tuple[Paper, str] | None:
    title = _str(front.get("title"))
    if not title:
        return None

    raw_authors = front.get("authors")
    authors = (
        tuple(str(a).strip() for a in raw_authors if str(a).strip())
        if isinstance(raw_authors, list)
        else ()
    )

    paper = Paper(
        title=title,
        authors=authors,
        year=_int(front.get("year")),
        doi=_str(front.get("doi")),
        venue=_str(front.get("venue")),
        url=_str(front.get("url")),
        citations=_int(front.get("citations")),
        open_access=_str(front.get("open_access")),
        entry_type=_str(front.get("entry_type")) or "article",
        pdf_url=_str(front.get("pdf_url")),
    )
    return paper, (_str(front.get("cite_key")) or fallback_key)


def read_all(*, papers_dir: Path) -> list[tuple[Paper, str]]:
    """Read every note in the papers folder."""
    if not papers_dir.is_dir():
        raise VaultError(
            f"{papers_dir} does not exist. Set VAULT_PAPERS_DIR, or create the folder."
        )
    entries: list[tuple[Paper, str]] = []
    for path in sorted(papers_dir.glob("*.md")):
        front, _ = _split_frontmatter(path.read_text(encoding="utf-8"))
        entry = _note_to_paper(front, path.stem)
        if entry is not None:
            entries.append(entry)
    return entries
