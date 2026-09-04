"""The record of what was looked at and turned down.

`expand` acquires; nothing recorded a decision. A paper deleted by hand came
back on the next run, because candidates were filtered only against the notes
currently present. So a vault grew by harvest and shrank by deletion, and the
difference between the papers that were chosen and the ones that merely arrived
was not written down anywhere.

This is a sidecar, not frontmatter, and deliberately: an exclusion is a fact
about the search process, not an intrinsic property of the paper, and the note
format bars judgements from frontmatter for exactly that reason. It is also not
a second source of truth. A note that exists always wins; the ledger only says
what was *not* included, and `expand --report` surfaces a disagreement rather
than resolving one.

Append-only is a promise about this module, not an enforcement against a text
editor. Nothing here opens the file for writing except :func:`append`, which
opens it for appending; there is no rewrite path and no compaction. A superseded
decision is a new row, and :func:`load` folds by last-row-wins -- the opposite
of ``vault.index``'s ``setdefault``, and on purpose.

That promise is also why the row grew from nine columns to thirteen without a
migration: :func:`parse_row` sniffs the version off the field count, so a file
written before v2 keeps its own version line and header and simply gains v2
rows below them.
"""

from __future__ import annotations

import collections
import datetime as dt
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

SCREENING_FILENAME: Final = "screening.tsv"
VERSION_LINE: Final = "# research-assistant screening v2"

# Fixed-alphabet columns first, the free-text ones last, `title` last of all.
# That ordering *is* the escaping strategy: no csv module and no quoting, so a
# stray tab introduced by hand can only ever damage the readable column.
HEADER_V1: Final[tuple[str, ...]] = (
    "decided",
    "decision",
    "openalex_id",
    "doi",
    "year",
    "via",
    "seeds",
    "reason",
    "title",
)

# v2 adds the four columns the reading list renders, so triage reads off the
# ledger without a second lookup. Rows are sniffed by field count rather than
# migrated: `screening.py` has no rewrite path, and gaining one for this would
# be a worse trade than a branch in the parser.
HEADER: Final[tuple[str, ...]] = (
    "decided",
    "decision",
    "openalex_id",
    "doi",
    "year",
    "citations",
    "via",
    "seeds",
    "pdf_url",
    "venue",
    "authors",
    "reason",
    "title",
)

INCLUDE: Final = "include"
EXCLUDE: Final = "exclude"
PENDING: Final = "pending"
DECISIONS: Final[frozenset[str]] = frozenset({INCLUDE, EXCLUDE, PENDING})

# Quoting would let a title carry a newline, and then the file stops being
# greppable and `wc -l`-able, which is most of why it is a TSV.
_TITLE_LIMIT: Final = 200


class ScreeningError(Exception):
    """Raised when the ledger cannot be read from where it should be."""


@dataclass(frozen=True, slots=True)
class Decision:
    """One row: what was decided about one work, and when."""

    decided: str
    decision: str
    openalex_id: str | None = None
    doi: str | None = None
    year: int | None = None
    citations: int | None = None
    via: tuple[str, ...] = ()
    seeds: tuple[str, ...] = ()
    pdf_url: str | None = None
    venue: str = ""
    authors: tuple[str, ...] = ()
    reason: str = ""
    title: str = ""


@dataclass(frozen=True, slots=True)
class Ledger:
    """Every row, and the standing decision each identifier resolves to."""

    path: Path
    rows: tuple[Decision, ...] = ()
    by_openalex: Mapping[str, Decision] = field(default_factory=dict)
    by_doi: Mapping[str, Decision] = field(default_factory=dict)
    unreadable: int = 0

    def lookup(self, *, openalex_id: str | None, doi: str | None) -> Decision | None:
        """The standing decision for a work, by either identifier."""
        if openalex_id and openalex_id in self.by_openalex:
            return self.by_openalex[openalex_id]
        if doi and doi.lower() in self.by_doi:
            return self.by_doi[doi.lower()]
        return None

    def decided(self, *, openalex_id: str | None, doi: str | None) -> bool:
        """Whether a decision stands. ``pending`` is not one: it still suppresses
        nothing, so `expand` offers the work again."""
        found = self.lookup(openalex_id=openalex_id, doi=doi)
        return found is not None and found.decision in {INCLUDE, EXCLUDE}

    def seen(self, *, openalex_id: str | None, doi: str | None) -> bool:
        """Whether any row stands, ``pending`` included.

        What `expand` asks before recording a candidate. A pending row now
        renders in the reading list, so re-offering it would only grow the
        ledger by the whole candidate set on every run.
        """
        return self.lookup(openalex_id=openalex_id, doi=doi) is not None


def now() -> str:
    """A full UTC timestamp, so ordering is total and file order only breaks ties."""
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def _flat(value: str, *, limit: int | None = None) -> str:
    """One line, one space between words: what makes the TSV safe without quoting."""
    text = " ".join(str(value).split())
    return text[:limit] if limit else text


def ledger_path(papers_dir: Path) -> Path:
    """A sibling of ``pdfs/`` and ``topics/``, never inside the papers folder."""
    return papers_dir.parent / SCREENING_FILENAME


def format_row(decision: Decision) -> str:
    """One tab-separated line, terminated. Always v2's thirteen columns. Pure."""
    fields = (
        decision.decided,
        decision.decision,
        decision.openalex_id or "",
        decision.doi or "",
        "" if decision.year is None else str(decision.year),
        "" if decision.citations is None else str(decision.citations),
        ";".join(decision.via),
        ";".join(decision.seeds),
        decision.pdf_url or "",
        _flat(decision.venue, limit=_TITLE_LIMIT),
        _flat("; ".join(decision.authors), limit=_TITLE_LIMIT),
        _flat(decision.reason),
        _flat(decision.title, limit=_TITLE_LIMIT),
    )
    return "\t".join(_flat(f) for f in fields) + "\n"


def _rejoin(parts: list[str], width: int) -> list[str]:
    """Pad to ``width``, or fold every extra field back into the last one."""
    if len(parts) < width:
        return parts + [""] * (width - len(parts))
    # Extra tabs can only have come from a hand-edited title.
    return [*parts[: width - 1], "\t".join(parts[width - 1 :])]


def parse_row(line: str) -> Decision | None:
    """One line back into a decision, or ``None`` if it cannot be read. Pure.

    Version-sniffed by field count, so v1 and v2 rows coexist in one file and
    append-only is never broken: exactly 9 or a hand-split 10-12 is v1, and 13
    or more is v2. A v2 writer always emits exactly 13, so the 10-12 window can
    only be a v1 row someone put a tab in.
    """
    if not line.strip() or line.startswith("#") or line.startswith(HEADER[0] + "\t"):
        return None
    raw = line.rstrip("\n").split("\t")
    v2 = len(raw) >= len(HEADER)
    parts = _rejoin(raw, len(HEADER) if v2 else len(HEADER_V1))

    if v2:
        (
            decided,
            decision,
            openalex_id,
            doi,
            year,
            citations,
            via,
            seeds,
            pdf_url,
            venue,
            authors,
            reason,
            title,
        ) = parts
    else:
        decided, decision, openalex_id, doi, year, via, seeds, reason, title = parts
        citations = pdf_url = venue = authors = ""
    if decision not in DECISIONS or not decided.strip():
        return None
    return Decision(
        decided=decided.strip(),
        decision=decision,
        openalex_id=openalex_id.strip() or None,
        doi=doi.strip().lower() or None,
        year=int(year) if year.strip().isdigit() else None,
        citations=int(citations) if citations.strip().isdigit() else None,
        via=tuple(v for v in via.split(";") if v),
        seeds=tuple(s for s in seeds.split(";") if s),
        pdf_url=pdf_url.strip() or None,
        venue=venue.strip(),
        authors=tuple(a.strip() for a in authors.split(";") if a.strip()),
        reason=reason.strip(),
        title=title.strip(),
    )


def load(papers_dir: Path) -> Ledger:
    """Read the ledger, folding repeated identifiers to their last row."""
    stray = papers_dir / SCREENING_FILENAME
    if stray.is_file():
        raise ScreeningError(
            f"{stray} is inside the papers folder. The ledger is a sibling of "
            f"pdfs/ and topics/: move it to {ledger_path(papers_dir)}."
        )

    path = ledger_path(papers_dir)
    if not path.is_file():
        return Ledger(path=path)

    rows: list[Decision] = []
    unreadable = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#") or line.startswith("decided\t"):
            continue
        parsed = parse_row(line)
        if parsed is None:
            unreadable += 1
        else:
            rows.append(parsed)

    by_openalex: dict[str, Decision] = {}
    by_doi: dict[str, Decision] = {}
    for row in rows:  # last row wins, so a change of mind is just a new row
        if row.openalex_id:
            by_openalex[row.openalex_id] = row
        if row.doi:
            by_doi[row.doi] = row
    return Ledger(
        path=path,
        rows=tuple(rows),
        by_openalex=by_openalex,
        by_doi=by_doi,
        unreadable=unreadable,
    )


def append(papers_dir: Path, decisions: Sequence[Decision]) -> int:
    """Add rows. Creates the file with its version line and header if absent.

    Never dedupes: an identical re-decision is a legitimate row, and the fold in
    :func:`load` is what collapses it.
    """
    if not decisions:
        return 0
    path = ledger_path(papers_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        if new:
            handle.write(f"{VERSION_LINE}\n{chr(9).join(HEADER)}\n")
        for decision in decisions:
            handle.write(format_row(decision))
    return len(decisions)


def counts(ledger: Ledger) -> collections.Counter[str]:
    """How many works carry each standing decision."""
    standing: dict[int, Decision] = {}
    for row in ledger.rows:
        # A row keyed on both identifiers is one work, not two.
        found = ledger.lookup(openalex_id=row.openalex_id, doi=row.doi)
        if found is not None:
            standing[id(found)] = found
    return collections.Counter(row.decision for row in standing.values())


def pending(ledger: Ledger) -> tuple[Decision, ...]:
    """The rows whose standing decision is ``pending``, in ledger order."""
    return tuple(
        row
        for row in ledger.rows
        if row.decision == PENDING
        and ledger.lookup(openalex_id=row.openalex_id, doi=row.doi) is row
    )
