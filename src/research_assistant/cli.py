"""Reference management for an Obsidian vault: Crossref/OpenAlex to notes to BibTeX."""

from __future__ import annotations

import collections
import datetime as dt
import json
import re
from dataclasses import dataclass

# Typer resolves these annotations at runtime through get_type_hints(), so
# `Path` cannot move into the type-checking block despite only appearing in
# annotations: --papers-dir and --out would both stop resolving.
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Annotated, Any

import httpx
import typer

from research_assistant import (
    bibtex,
    graph,
    highlights,
    screening,
    search,
    sources,
    vault,
)
from research_assistant.models import Paper

if TYPE_CHECKING:
    from collections.abc import Container, Sequence

app = typer.Typer(add_completion=False, help=__doc__)

# Set once by the callback below, before any command body runs. There is no
# default: the vault lives outside this tree and guessing at someone's folder
# layout is worse than saying so.
_PAPERS_DIR: Path | None = None


@app.callback()
def _main(
    papers_dir: Annotated[
        Path | None,
        typer.Option(
            "--papers-dir",
            envvar="VAULT_PAPERS_DIR",
            help="The vault folder holding one note per paper.",
        ),
    ] = None,
) -> None:
    global _PAPERS_DIR
    _PAPERS_DIR = papers_dir


def _papers_dir() -> Path:
    """The vault folder holding one note per paper."""
    if _PAPERS_DIR is None:
        typer.secho(
            "No papers folder: pass --papers-dir or set VAULT_PAPERS_DIR.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    return _PAPERS_DIR.expanduser()


def _describe(record: Paper) -> None:
    """Echo the three lines that let you check the lookup found the right thing."""
    typer.echo(f"  {record.title}")
    if record.authors:
        shown = ", ".join(record.authors[:3])
        suffix = " et al." if len(record.authors) > 3 else ""
        typer.echo(f"  {shown}{suffix}")
    typer.echo(f"  {record.venue or 'unknown venue'} ({record.year or 'n.d.'})")


def _unclaimed_key(key: str, taken: Container[str]) -> str:
    """A cite key no note in the vault already claims."""
    while key in taken:
        key = f"{key}a"  # two papers, same author, year and first word
    return key


def _existing_note(
    record: Paper, openalex_id: str | None, *, papers_dir: Path
) -> Path | None:
    """The note already holding this DOI or OpenAlex id, if the vault has one."""
    by_doi, by_openalex = vault.index(papers_dir)
    if record.doi and (path := by_doi.get(record.doi.lower())) is not None:
        return path
    return by_openalex.get(openalex_id) if openalex_id else None


@app.command()
def paper(
    source: Annotated[
        str,
        typer.Argument(
            help="DOI or OpenAlex id (W123), bare or as a doi.org/openalex.org URL"
        ),
    ],
    force: Annotated[
        bool, typer.Option("--force", help="Add even if the source is already present")
    ] = False,
) -> None:
    """Look up a DOI or OpenAlex id and add it to the Obsidian research notes.

    An OpenAlex id is the way in for work that has no DOI at all: USENIX and
    several other proceedings mint none, so OpenAlex holds the only identifier.
    """
    with httpx.Client(follow_redirects=True) as client:
        try:
            record, openalex_id = sources.resolve_source(source, client=client)
        except sources.SourceLookupError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc

        _describe(record)

        papers_dir = _papers_dir()
        key = _unclaimed_key(record.cite_key(), vault.cite_keys(papers_dir))
        if key != record.cite_key():
            typer.secho(
                f"Cite key {record.cite_key()} is taken; using {key}.",
                fg=typer.colors.YELLOW,
            )
        try:
            if not force:
                existing = _existing_note(record, openalex_id, papers_dir=papers_dir)
                if existing is not None:
                    typer.secho(
                        f"Already in the vault at {existing}; "
                        "use --force to add anyway.",
                        fg=typer.colors.YELLOW,
                    )
                    raise typer.Exit(0)

            pdf_name = _save_pdf(record, key, papers_dir=papers_dir, client=client)
            path = vault.create_paper(
                record,
                key,
                papers_dir=papers_dir,
                pdf_name=pdf_name,
                openalex_id=openalex_id,
            )
        except vault.VaultError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc

    typer.secho(f"Added: {path}", fg=typer.colors.GREEN)
    typer.echo("Write your notes and topics in Obsidian.")


@app.command()
def source(
    title: Annotated[str, typer.Option("--title", help="Title of the resource")],
    author: Annotated[
        list[str],
        typer.Option("--author", help="Author, repeated once per author, in order"),
    ],
    year: Annotated[int | None, typer.Option("--year", help="Year of publication")],
    venue: Annotated[
        str | None,
        typer.Option("--venue", help="Where it appeared: talk, vendor, site, report"),
    ] = None,
    url: Annotated[str | None, typer.Option("--url", help="Where to find it")] = None,
    entry_type: Annotated[
        str,
        typer.Option("--type", help="BibTeX entry type: misc, techreport, article, …"),
    ] = "misc",
    pdf: Annotated[
        Path | None,
        typer.Option(
            "--pdf", exists=True, dir_okay=False, help="A PDF to file with it"
        ),
    ] = None,
    key: Annotated[
        str | None,
        typer.Option("--key", help="Cite key, when the derived one reads badly"),
    ] = None,
) -> None:
    """Record a resource that no index knows about, by hand.

    Crossref and OpenAlex cover journals and proceedings and nothing else, but
    a dissertation cites more than that: a supervisor's slide deck, a vendor
    technical guide, a datasheet, a standard. Those are still evidence, and a
    note is the only place they can be cited from. Everything here is what you
    typed, so there is no lookup and nothing to be deterministic about.
    """
    record = Paper(
        title=" ".join(title.split()),
        authors=tuple(author),
        year=year,
        venue=venue,
        url=url,
        entry_type=entry_type,
    )
    _describe(record)

    papers_dir = _papers_dir()
    # A corporate author has no surname, so the derived key takes the last word
    # of the organisation's name: "distribuidas2020waspmote" for Libelium.
    claimed = vault.cite_keys(papers_dir)
    if key is not None:
        cite_key = key
        if cite_key in claimed:
            # A hand-picked key is the point of --key, so this reports and does
            # not repair: only you know whether the clash is the same resource.
            typer.secho(
                f"{cite_key} is already claimed by {claimed[cite_key][0].name}.",
                fg=typer.colors.YELLOW,
            )
    else:
        cite_key = _unclaimed_key(record.cite_key(), claimed)
    try:
        pdf_name = (
            vault.save_pdf(
                cite_key,
                pdf.read_bytes(),
                pdfs_dir=papers_dir.parent / vault.PDFS_DIRNAME,
            ).name
            if pdf is not None
            else None
        )
        path = vault.create_paper(
            record, cite_key, papers_dir=papers_dir, pdf_name=pdf_name
        )
    except vault.VaultError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    typer.secho(f"Added: {path}", fg=typer.colors.GREEN)
    typer.echo("Write your notes and topics in Obsidian.")


def _save_pdf(
    record: Paper, key: str, *, papers_dir: Path, client: httpx.Client
) -> str | None:
    """Download the open-access PDF beside the notes, if one can be had."""
    if not record.pdf_url:
        return None
    data = sources.fetch_pdf(record.pdf_url, client=client)
    if data is None:
        typer.secho(
            f"  no PDF: {record.pdf_url} did not serve one. Save it by hand into "
            f"{papers_dir.parent / vault.PDFS_DIRNAME}/{key}.pdf",
            fg=typer.colors.YELLOW,
        )
        return None
    path = vault.save_pdf(key, data, pdfs_dir=papers_dir.parent / vault.PDFS_DIRNAME)
    typer.echo(f"  PDF: {path}")
    return path.name


_KEY_SHAPE = re.compile(r"^[a-z0-9]+$")


def _print_key_audit(papers_dir: Path) -> bool:
    """Report cite keys the vault cannot spell or claims twice. True if clean."""
    claimed = vault.cite_keys(papers_dir)
    collisions = {key: paths for key, paths in claimed.items() if len(paths) > 1}
    malformed = sorted(key for key in claimed if not _KEY_SHAPE.match(key))

    total = sum(len(paths) for paths in claimed.values())
    typer.secho(
        f"{total} notes · {len(claimed)} cite keys · {len(collisions)} collision(s)",
        fg=typer.colors.CYAN,
    )
    for key, paths in sorted(collisions.items()):
        typer.secho(f"  {key}", fg=typer.colors.YELLOW)
        for path in paths:
            typer.echo(f"    {path.name}")
    if malformed:
        typer.secho(
            f"\n{len(malformed)} key(s) are not [a-z0-9]: {', '.join(malformed[:8])}",
            fg=typer.colors.YELLOW,
        )
    return not collisions and not malformed


@app.command()
def bib(
    out: Annotated[
        Path | None, typer.Option("--out", help="Where to write the bibliography")
    ] = None,
    check: Annotated[
        bool,
        typer.Option("--check", help="Audit the cite keys and write nothing"),
    ] = False,
) -> None:
    """Regenerate a BibTeX bibliography from the Obsidian vault."""
    papers_dir = _papers_dir()
    if check:
        if out is not None:
            typer.secho(
                "Give either --out <file> or --check.", fg=typer.colors.RED, err=True
            )
            raise typer.Exit(1)
        raise typer.Exit(0 if _print_key_audit(papers_dir) else 1)
    if out is None:
        typer.secho(
            "Give either --out <file> or --check.", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(1)

    try:
        entries = vault.read_all(papers_dir=papers_dir)
    except vault.VaultError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    try:
        rendered = bibtex.render(entries)
    except bibtex.BibtexError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        typer.secho("Run `bib --check` to see which notes.", fg=typer.colors.YELLOW)
        raise typer.Exit(1) from exc

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8")
    typer.secho(f"Wrote {len(entries)} entries to {out}", fg=typer.colors.GREEN)


HARVESTED_TAG = "harvested"
SEED_TAG = "seed"


def _tags_for(
    subfields: Sequence[str], *, harvested: bool, existing: Sequence[str] = ()
) -> list[str]:
    """``paper`` plus provenance plus one nested tag per OpenAlex subfield.

    Tags this function does not own are carried through. ``read`` is derived
    from the body, so an unrelated command rewriting the list must not retract
    it until the next ``tidy`` puts it back.
    """
    tags = ["paper", HARVESTED_TAG if harvested else SEED_TAG]
    tags.extend(f"topic/{slug}" for slug in subfields)
    owned = {"paper", HARVESTED_TAG, SEED_TAG}
    tags.extend(
        tag
        for tag in existing
        if tag not in owned and not tag.startswith("topic/") and tag not in tags
    )
    return tags


def _root_notes(papers_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Notes to expand from: everything a previous run did not add itself."""
    roots: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(papers_dir.glob("*.md")):
        front = vault.read_frontmatter(path)
        tags = front.get("tags")
        if isinstance(tags, list) and HARVESTED_TAG in tags:
            continue
        if front.get("doi") or front.get("openalex_id"):
            roots.append((path, front))
    return roots


def _resolve_roots(
    roots: list[tuple[Path, dict[str, Any]]],
    *,
    client: httpx.Client,
    backfill: bool = True,
) -> list[dict[str, Any]]:
    """Fetch the OpenAlex work for each root, and backfill its ``openalex_id``.

    ``backfill`` is off under ``--dry-run``, which must leave the vault alone.
    """
    known = [str(f["openalex_id"]) for _, f in roots if f.get("openalex_id")]
    dois = [
        str(f["doi"]) for _, f in roots if f.get("doi") and not f.get("openalex_id")
    ]

    works = graph.fetch_works(known, client=client) if known else []
    if dois:
        works.extend(graph.fetch_by_doi(dois, client=client))

    by_doi = {
        doi: work for work in works if (doi := _work_doi(work))
    }  # backfill lookup
    for path, front in roots:
        if front.get("openalex_id") or not backfill:
            continue
        doi = str(front.get("doi", "")).lower()
        work = by_doi.get(doi)
        if work is not None:
            vault.update_frontmatter(
                path, {"openalex_id": graph.bare_id(str(work.get("id", "")))}
            )
    return works


def _work_doi(work: dict[str, Any]) -> str | None:
    doi = work.get("doi")
    if not isinstance(doi, str) or not doi.strip():
        return None
    return doi.strip().removeprefix("https://doi.org/").lower()


def _decision_for(
    candidate: graph.Candidate,
    works: dict[str, dict[str, Any]],
    stamped: str,
    decision: str,
    reason: str,
    record: Paper | None = None,
) -> screening.Decision:
    """One ledger row for one candidate, labelled from whatever metadata is here."""
    work = works.get(candidate.openalex_id, {})
    year = work.get("publication_year")
    return screening.Decision(
        decided=stamped,
        decision=decision,
        openalex_id=candidate.openalex_id,
        # The fetched work is the only place a backward candidate's DOI appears.
        doi=candidate.doi or _work_doi(work),
        year=year if isinstance(year, int) else None,
        via=tuple(sorted(candidate.provenance)),
        seeds=tuple(sorted(candidate.seeds)),
        reason=reason,
        title=record.title if record else str(work.get("title") or ""),
    )


def _adopt_ledger(papers_dir: Path, ledger: screening.Ledger) -> int:
    """Seed the ledger from a vault that predates it. Every note is an inclusion."""
    if ledger.rows:
        typer.secho(
            f"{ledger.path} already has {len(ledger.rows)} row(s); "
            "adopting again would double-count.",
            fg=typer.colors.RED,
            err=True,
        )
        return 1

    rows: list[screening.Decision] = []
    for path in sorted(papers_dir.glob("*.md")):
        front = vault.read_frontmatter(path)
        openalex_id = front.get("openalex_id")
        doi = front.get("doi")
        if not openalex_id and not doi:
            continue
        year = front.get("year")
        rows.append(
            screening.Decision(
                # The file's own mtime: when the decision was actually made.
                decided=dt.datetime.fromtimestamp(path.stat().st_mtime, dt.UTC)
                .replace(microsecond=0)
                .isoformat(),
                decision=screening.INCLUDE,
                openalex_id=str(openalex_id) if openalex_id else None,
                doi=str(doi).lower() if doi else None,
                year=year if isinstance(year, int) else None,
                # The history of these decisions is unrecoverable, and saying
                # "adopted" is more honest than inventing one.
                reason="adopted",
                title=str(front.get("title") or ""),
            )
        )
    screening.append(papers_dir, rows)
    typer.secho(
        f"Adopted {len(rows)} note(s) into {screening.ledger_path(papers_dir)}.",
        fg=typer.colors.GREEN,
    )
    return 0


def _record_decisions(
    papers_dir: Path, include: Sequence[str], exclude: Sequence[str], reason: str
) -> int:
    """Record decisions for ids named on the command line."""
    wanted = [
        (sources.as_openalex_id(raw) or None, raw) for raw in [*include, *exclude]
    ]
    stamped = screening.now()
    rows = [
        screening.Decision(
            decided=stamped,
            decision=screening.INCLUDE if raw in set(include) else screening.EXCLUDE,
            openalex_id=ident,
            doi=None if ident else sources.normalize_doi(raw),
            reason=reason,
        )
        for ident, raw in wanted
    ]
    screening.append(papers_dir, rows)
    typer.secho(f"Recorded {len(rows)} decision(s).", fg=typer.colors.GREEN)
    return 0


def _report_screening(papers_dir: Path, ledger: screening.Ledger) -> int:
    """The four PRISMA numbers, and where the vault and the ledger disagree."""
    by_doi, by_openalex = vault.index(papers_dir)
    included = len(list(papers_dir.glob("*.md")))

    def has_note(row: screening.Decision) -> bool:
        return bool(
            (row.openalex_id and row.openalex_id in by_openalex)
            or (row.doi and row.doi in by_doi)
        )

    standing = [
        row
        for row in ledger.rows
        if ledger.lookup(openalex_id=row.openalex_id, doi=row.doi) is row
    ]
    excluded = [r for r in standing if r.decision == screening.EXCLUDE]
    pending = [r for r in standing if r.decision == screening.PENDING]
    screened = len(excluded) + len(
        [r for r in standing if r.decision == screening.INCLUDE]
    )

    unrecorded = included - len([r for r in standing if has_note(r)])
    contradicted = [r for r in excluded if has_note(r)]
    vanished = [
        r for r in standing if r.decision == screening.INCLUDE and not has_note(r)
    ]

    typer.secho(
        f"Identified {len(standing) + max(unrecorded, 0):>5}   "
        "distinct works ever seen\n"
        f"Screened   {screened:>5}   carrying a standing decision\n"
        f"Excluded   {len(excluded):>5}\n"
        f"Included   {included:>5}   notes in the vault\n"
        f"Pending    {len(pending):>5}   awaiting a decision",
        fg=typer.colors.CYAN,
    )
    typer.echo("")
    typer.echo(f"  in the vault, not in the ledger   {max(unrecorded, 0):>4}")
    typer.echo(f"  excluded, but a note exists       {len(contradicted):>4}")
    typer.echo(f"  included, but no note exists      {len(vanished):>4}")
    typer.echo(f"  rows the parser could not read    {ledger.unreadable:>4}")

    if unrecorded > 0 and not ledger.rows:
        typer.secho(
            "\nThe ledger is empty. Seed it with `expand --adopt`.",
            fg=typer.colors.YELLOW,
        )
    for row in vanished[:5]:
        # Deleting the note *was* the decision; the ledger's job is only not to
        # undo it. The row is now wrong, but only you know why it went.
        ident = row.openalex_id or row.doi or ""
        typer.secho(
            f'\n  expand --exclude {ident} --reason "deleted by hand"',
            fg=typer.colors.YELLOW,
        )
    return 0


@app.command()
def expand(
    backward: Annotated[
        bool, typer.Option("--backward/--no-backward", help="Papers the roots cite")
    ] = True,
    forward: Annotated[
        bool, typer.Option("--forward/--no-forward", help="Papers citing the roots")
    ] = True,
    related: Annotated[
        bool, typer.Option("--related/--no-related", help="OpenAlex related works")
    ] = True,
    pdfs: Annotated[
        bool, typer.Option("--pdfs/--no-pdfs", help="Download open-access PDFs")
    ] = True,
    min_year: Annotated[
        int | None, typer.Option("--min-year", help="Skip anything older")
    ] = None,
    limit: Annotated[
        int | None, typer.Option("--limit", help="Stop after this many new notes")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="List candidates, write nothing")
    ] = False,
    screen: Annotated[
        bool,
        typer.Option("--screen", help="Record candidates as pending, write no notes"),
    ] = False,
    include: Annotated[
        list[str] | None,
        typer.Option("--include", help="Record a decision to include these ids"),
    ] = None,
    exclude: Annotated[
        list[str] | None,
        typer.Option("--exclude", help="Record a decision to exclude these ids"),
    ] = None,
    reason: Annotated[
        str | None, typer.Option("--reason", help="Why; required with --exclude")
    ] = None,
    report: Annotated[
        bool, typer.Option("--report", help="Reconcile the vault against the ledger")
    ] = False,
    adopt: Annotated[
        bool, typer.Option("--adopt", help="Seed the ledger from the existing vault")
    ] = False,
) -> None:
    """Add the papers around the vault: what it cites, cites it, and near it.

    Roots are every note *without* the ``harvested`` tag, so re-running never
    walks out to depth 2. To go deeper, drop that tag from the paper worth
    expanding and run again.
    """
    papers_dir = _papers_dir()
    if not papers_dir.is_dir():
        typer.secho(f"{papers_dir} does not exist.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    terminal = [dry_run, screen, report, adopt, bool(include or exclude)]
    if sum(terminal) > 1:
        typer.secho(
            "Give one of --dry-run, --screen, --report, --adopt, "
            "or --include/--exclude.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    if exclude and not reason:
        typer.secho(
            "--exclude needs --reason: a decision with no reason is not a record.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    try:
        ledger = screening.load(papers_dir)
    except screening.ScreeningError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    if adopt:
        raise typer.Exit(_adopt_ledger(papers_dir, ledger))
    if report:
        raise typer.Exit(_report_screening(papers_dir, ledger))
    if include or exclude:
        raise typer.Exit(
            _record_decisions(papers_dir, include or [], exclude or [], reason or "")
        )

    roots = _root_notes(papers_dir)
    if not roots:
        typer.secho(
            "No root notes: every note is tagged 'harvested', or none has a DOI.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(0)
    typer.echo(f"Expanding from {len(roots)} root note(s).")

    with httpx.Client(follow_redirects=True) as client:
        seed_works = _resolve_roots(roots, client=client, backfill=not dry_run)
        candidates = graph.harvest(
            seed_works,
            client=client,
            backward=backward,
            forward=forward,
            related=related,
        )
        by_doi, by_openalex = vault.index(papers_dir)
        fresh: dict[str, graph.Candidate] = {}
        screened = 0
        for ident, candidate in candidates.items():
            # The note check runs first: a note that exists always beats the
            # ledger, because adding it *was* the change of mind. `--report`
            # surfaces the disagreement rather than the code resolving it.
            if ident in by_openalex or (candidate.doi and candidate.doi in by_doi):
                continue
            if ledger.decided(openalex_id=ident, doi=candidate.doi):
                screened += 1
                continue
            fresh[ident] = candidate
        typer.echo(
            f"{len(candidates)} candidate(s), {len(fresh)} new, "
            f"{screened} already screened."
        )
        if not fresh:
            raise typer.Exit(0)

        works = {
            graph.bare_id(str(work.get("id", ""))): work
            for work in graph.fetch_works(sorted(fresh), client=client)
        }
        kept = _select(fresh, works, by_doi, ledger, min_year=min_year)
        selected = kept[:limit] if limit is not None else kept
        deferred = len(kept) - len(selected)

        if dry_run or screen:
            _print_candidates(selected, works)
            if deferred:
                typer.secho(
                    f"  … {deferred} more deferred by --limit",
                    fg=typer.colors.BRIGHT_BLACK,
                )
        if dry_run:
            typer.secho(
                f"\nDry run: {len(selected)} note(s) would be written.",
                fg=typer.colors.YELLOW,
            )
            raise typer.Exit(0)
        if screen:
            stamped = screening.now()
            written_rows = screening.append(
                papers_dir,
                [
                    _decision_for(c, works, stamped, screening.PENDING, "")
                    for c in selected
                ],
            )
            typer.secho(
                f"\nRecorded {written_rows} candidate(s) as pending. "
                f"Decide with `expand --include` / `--exclude`.",
                fg=typer.colors.GREEN,
            )
            raise typer.Exit(0)

        written, no_pdf = _write_notes(
            selected, works, papers_dir=papers_dir, client=client, pdfs=pdfs
        )
        stamped = screening.now()
        screening.append(
            papers_dir,
            [
                _decision_for(
                    entry.candidate, works, stamped, screening.INCLUDE, "", entry.record
                )
                for entry in written
            ],
        )

    for path, front in roots:
        subfields = _subfields_of(front)
        held = front.get("tags")
        vault.update_frontmatter(
            path,
            {
                "tags": _tags_for(
                    subfields,
                    harvested=False,
                    existing=[str(tag) for tag in held]
                    if isinstance(held, list)
                    else (),
                )
            },
        )

    typer.secho(
        f"\nWrote {len(written)} note(s) to {papers_dir}", fg=typer.colors.GREEN
    )
    if no_pdf:
        typer.secho(
            f"{len(no_pdf)} had no fetchable PDF; see the 'Missing PDF' view.",
            fg=typer.colors.YELLOW,
        )
    typer.echo("Now run the `relink` command.")


def _subfields_of(front: dict[str, Any]) -> tuple[str, ...]:
    """Keep a root note's existing topic tags rather than blanking them."""
    tags = front.get("tags")
    if not isinstance(tags, list):
        return ()
    return tuple(
        str(tag).removeprefix("topic/") for tag in tags if str(tag).startswith("topic/")
    )


def _select(
    fresh: dict[str, graph.Candidate],
    works: dict[str, dict[str, Any]],
    by_doi: dict[str, Path],
    ledger: screening.Ledger | None = None,
    *,
    min_year: int | None,
) -> list[graph.Candidate]:
    """Apply the year filter and drop works already present or already screened.

    Does *not* cap: `--limit` defers rather than decides, so the caller slices
    the result and can report what it deferred without writing a ledger row for
    a paper nobody judged.
    """
    kept: list[graph.Candidate] = []
    for ident, candidate in fresh.items():
        work = works.get(ident)
        if work is None:
            continue  # merged or deleted in OpenAlex
        doi = _work_doi(work)
        if doi and doi in by_doi:
            continue  # the candidate carried no DOI but the fetched work does
        # Same second chance for the ledger. A backward or related candidate
        # always arrives with `doi=None`, so this is the only place its DOI is
        # ever known, and an exclusion recorded against the DOI must still bite.
        if ledger is not None and ledger.decided(openalex_id=ident, doi=doi):
            continue
        year = work.get("publication_year")
        if min_year is not None and (not isinstance(year, int) or year < min_year):
            continue
        kept.append(candidate)
    kept.sort(
        key=lambda c: (
            -(works[c.openalex_id].get("publication_year") or 0),
            works[c.openalex_id].get("title") or "",
        )
    )
    return kept


def _print_candidates(
    selected: Sequence[graph.Candidate], works: dict[str, dict[str, Any]]
) -> None:
    for candidate in selected:
        work = works[candidate.openalex_id]
        year = work.get("publication_year") or "n.d."
        routes = ",".join(sorted(candidate.provenance))
        title = " ".join(str(work.get("title") or "Untitled").split())
        typer.echo(f"  {year}  [{routes}]  {title[:88]}")


@dataclass(frozen=True, slots=True)
class Written:
    """A note `expand` just created, and what it took to create it."""

    path: Path
    key: str
    candidate: graph.Candidate
    record: Paper


def _write_notes(
    selected: Sequence[graph.Candidate],
    works: dict[str, dict[str, Any]],
    *,
    papers_dir: Path,
    client: httpx.Client,
    pdfs: bool,
) -> tuple[list[Written], list[str]]:
    """Resolve each candidate against Crossref and write its note."""
    written: list[Written] = []
    no_pdf: list[str] = []
    # Seeded from the vault, not empty: an unseeded set only stops two notes in
    # one run colliding, and says nothing about the two hundred already there.
    seen_keys: set[str] = set(vault.cite_keys(papers_dir))

    for position, candidate in enumerate(selected, start=1):
        work = works[candidate.openalex_id]
        doi = _work_doi(work)
        record: Paper | None = None
        if doi:
            try:
                record = sources.resolve(doi, client=client)
            except sources.SourceLookupError, httpx.HTTPError:
                record = None
        if record is None:
            # Crossref does not know it, or has no DOI to know it by.
            record = sources.paper_from_openalex(work)

        key = _unclaimed_key(record.cite_key(), seen_keys)
        seen_keys.add(key)

        topics, subfields = graph.topics_of(work)
        pdf_name = (
            _save_pdf(record, key, papers_dir=papers_dir, client=client)
            if pdfs
            else None
        )
        if pdf_name is None:
            no_pdf.append(key)
        try:
            path = vault.create_paper(
                record,
                key,
                papers_dir=papers_dir,
                pdf_name=pdf_name,
                openalex_id=candidate.openalex_id,
                topics=topics,
                tags=_tags_for(subfields, harvested=True),
            )
        except vault.VaultError as exc:
            typer.secho(f"  [{position}] skipped: {exc}", fg=typer.colors.YELLOW)
            continue
        written.append(Written(path=path, key=key, candidate=candidate, record=record))
        typer.echo(f"  [{position}/{len(selected)}] {path.name}")
    return written, no_pdf


@app.command()
def relink() -> None:
    """Rebuild ``cites`` and ``topics`` across the vault from the citation graph.

    Only links to notes that actually exist are written, so the graph has no
    unresolved links. Obsidian derives the reverse direction itself in the
    "Linked mentions" pane, which is why there is no ``cited_by`` to keep in
    sync. Idempotent, so it is safe to re-run after any expand.
    """
    papers_dir = _papers_dir()
    _backfill_openalex_ids(papers_dir)
    _, by_openalex = vault.index(papers_dir)
    if not by_openalex:
        typer.secho(
            "No note carries an openalex_id yet; run `expand` first.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(0)

    with httpx.Client(follow_redirects=True) as client:
        works = graph.fetch_works(sorted(by_openalex), client=client)

    # Count first: a topic earns a hub note, and so a place in the graph, only
    # once a second paper shares it.
    topics_of: dict[Path, tuple[str, ...]] = {}
    frequency: collections.Counter[str] = collections.Counter()
    for work in works:
        path = by_openalex.get(graph.bare_id(str(work.get("id", ""))))
        if path is None:
            continue
        names, _ = graph.topics_of(work)
        topics_of[path] = names
        frequency.update(names)
    shared = {t for t, n in frequency.items() if n >= vault.MIN_TOPIC_PAPERS}

    changed = 0
    members: dict[str, list[tuple[str, int | None, int | None]]] = (
        collections.defaultdict(list)
    )
    for work in works:
        ident = graph.bare_id(str(work.get("id", "")))
        path = by_openalex.get(ident)
        if path is None:
            continue
        cites = sorted(
            f"[[{target.stem}]]"
            for reference in work.get("referenced_works", [])
            if (target := by_openalex.get(graph.bare_id(str(reference)))) is not None
            and target != path
        )
        kept = [t for t in topics_of.get(path, ()) if t in shared]
        front = vault.read_frontmatter(path)
        for topic in kept:
            members[topic].append(
                (path.stem, front.get("year"), front.get("citations"))
            )
        # Links, not strings: a plain label is not a node, so however well it
        # groups a table it never reaches the graph.
        linked = [vault.topic_link(topic) for topic in kept]
        updates: dict[str, Any] = {"topics": linked, "cites": cites}
        # `WORK_FIELDS` already asks for these, and the response is already
        # here, so a note's citation count stopped being a snapshot from
        # whenever it was imported for the cost of reading three more keys.
        count = work.get("cited_by_count")
        if isinstance(count, int):
            updates["citations"] = count
        oa = work.get("open_access")
        if isinstance(oa, dict) and isinstance(oa.get("oa_status"), str):
            updates["open_access"] = str(oa["oa_status"]).lower()
        best = work.get("best_oa_location")
        if isinstance(best, dict) and isinstance(best.get("pdf_url"), str):
            # Only when OpenAlex has one: a gap upstream must not blank a URL
            # somebody recorded by hand.
            updates["pdf_url"] = str(best["pdf_url"])
        if vault.update_frontmatter(path, updates):
            changed += 1

    topics_dir = papers_dir.parent / vault.TOPICS_DIRNAME
    for topic, papers in sorted(members.items()):
        vault.write_topic_hub(topic, papers, topics_dir=topics_dir)

    typer.secho(
        f"Updated {changed} of {len(by_openalex)} notes.", fg=typer.colors.GREEN
    )
    typer.secho(
        f"Wrote {len(members)} topic hub(s) to {topics_dir}", fg=typer.colors.GREEN
    )


def _backfill_openalex_ids(
    papers_dir: Path, *, client: httpx.Client | None = None
) -> int:
    """Give every note with a DOI its OpenAlex id, so the linker can see it.

    ``paper`` records only what Crossref returns, so a note added by hand
    carries no ``openalex_id`` and would silently sit outside the citation
    graph. Doing this here rather than in ``paper`` keeps the single-paper path
    free of a second lookup, and repairs notes added before this existed.
    """
    # Lowercased to match `_work_doi`, which lowercases what OpenAlex returns.
    # A note recording its DOI in caps would otherwise never find its work.
    needing = {
        str(front["doi"]).lower(): path
        for path in sorted(papers_dir.glob("*.md"))
        if (front := vault.read_frontmatter(path)).get("doi")
        and not front.get("openalex_id")
    }
    if not needing:
        return 0

    if client is not None:
        works = graph.fetch_by_doi(sorted(needing), client=client)
    else:
        with httpx.Client(follow_redirects=True) as owned:
            works = graph.fetch_by_doi(sorted(needing), client=owned)

    repaired = 0
    for work in works:
        doi = _work_doi(work)
        path = needing.get(doi) if doi else None
        if path is not None and vault.update_frontmatter(
            path, {"openalex_id": graph.bare_id(str(work.get("id", "")))}
        ):
            repaired += 1
    if repaired:
        typer.echo(f"Backfilled openalex_id on {repaired} note(s).")
    return repaired


_BIBFIELDS = ("volume", "number", "pages", "publisher", "editors", "month")


def _backfill_bibfields(papers_dir: Path, *, client: httpx.Client | None = None) -> int:
    """Fill the fields a bibliography renders, from Crossref, for notes missing them.

    One request per note, unlike the abstract backfill which rides a batched
    OpenAlex call, which is why this is opt-in. Only unset fields are written,
    so an interrupted run is simply re-run.
    """
    needing = [
        (path, str(front["doi"]))
        for path in sorted(papers_dir.glob("*.md"))
        if (front := vault.read_frontmatter(path)).get("doi")
        and not all(front.get(field) for field in _BIBFIELDS)
    ]
    if not needing:
        return 0

    owned = client is None
    active = client or httpx.Client(follow_redirects=True)
    filled = 0
    try:
        with typer.progressbar(needing, label="Crossref") as progress:
            for path, doi in progress:
                try:
                    message = sources.fetch_crossref(
                        sources.normalize_doi(doi), client=active
                    )
                except sources.SourceLookupError, httpx.HTTPError:
                    continue
                record = sources.build_paper(message)
                front = vault.read_frontmatter(path)
                updates = {
                    field: value
                    for field in _BIBFIELDS
                    if not front.get(field)
                    and (
                        value := (
                            list(record.editors)
                            if field == "editors"
                            else getattr(record, field)
                        )
                    )
                }
                if updates and vault.update_frontmatter(path, updates):
                    filled += 1
    finally:
        if owned:
            active.close()
    return filled


@app.command()
def tidy(
    abstracts: Annotated[
        bool, typer.Option("--abstracts/--no-abstracts", help="Backfill from OpenAlex")
    ] = True,
    bibfields: Annotated[
        bool,
        typer.Option(
            "--bibfields",
            help="Backfill volume, issue, pages, publisher and editors from Crossref",
        ),
    ] = False,
) -> None:
    """Reformat note bodies to the template, and fill any abstract still missing.

    Notes were written by more than one code path over time and drifted into
    several whitespace shapes. This normalises them so a diff shows a changed
    sentence rather than a changed blank line. Section *content* is only ever
    reordered into the template, never rewritten, and headings the template does
    not know about are kept.

    It also syncs the derived ``read`` tag across the vault, so
    ``find --tag read`` lists what has actually been read through rather
    than merely collected.
    """
    papers_dir = _papers_dir()
    _, by_openalex = vault.index(papers_dir)

    filled: dict[Path, str] = {}
    if abstracts:
        missing = {
            ident: path
            for ident, path in by_openalex.items()
            if not vault.parse_body(
                vault._split_frontmatter(path.read_text(encoding="utf-8"))[1]
            ).get("Abstract")
        }
        if missing:
            with httpx.Client(follow_redirects=True) as client:
                for work in graph.fetch_works(sorted(missing), client=client):
                    path = missing.get(graph.bare_id(str(work.get("id", ""))))
                    text = sources._openalex_abstract(work)
                    if path is not None and text:
                        filled[path] = text

    # Publishers behind a bot check have to be saved from a browser, and the
    # `paper` command says so. Nothing then recorded the file, so adopt any PDF
    # whose name matches a cite key: the vault should reflect what is on disk.
    pdfs_dir = papers_dir.parent / vault.PDFS_DIRNAME
    on_disk = (
        {path.stem: path.name for path in pdfs_dir.glob("*.pdf")}
        if pdfs_dir.is_dir()
        else {}
    )

    enriched = _backfill_bibfields(papers_dir) if bibfields else 0

    reformatted = adopted = unescaped = retagged = reordered = 0
    for path in sorted(papers_dir.glob("*.md")):
        # Before the body work, so the note read below is already canonical.
        if vault.reorder_frontmatter(path):
            reordered += 1
        front, body = vault._split_frontmatter(path.read_text(encoding="utf-8"))
        sections = vault.parse_body(body)
        if path in filled:
            sections["Abstract"] = filled[path]
        # Notes added before the lookup resolved entities hold `Environmental
        # Science &amp; Technology` verbatim, which reaches refs.bib and
        # typesets as the entity rather than the ampersand.
        repairs = {
            field: sources.unescape(str(front[field]))
            for field in ("title", "venue")
            if front.get(field) and sources.unescape(str(front[field])) != front[field]
        }
        if repairs and vault.update_frontmatter(path, repairs):
            unescaped += 1
        if (text := sections.get("Abstract")) and (
            resolved := sources.unescape(text)
        ) != text:
            sections["Abstract"] = resolved
        name = on_disk.get(str(front.get("cite_key") or ""))
        if name and not front.get("pdf"):
            vault.update_frontmatter(path, {"pdf": f"[[{name}]]"})
            sections[vault.PDF_SECTION] = f"![[{name}]]"
            adopted += 1
        if vault.write_body(path, vault.render_body(sections)):
            reformatted += 1
        # The sweep is what backdates the tag: every note read before this
        # existed already carries the prose it is derived from.
        if vault.sync_read_tag(path, sections):
            retagged += 1

    typer.secho(
        f"Reformatted {reformatted} note(s); filled {len(filled)} abstract(s); "
        f"adopted {adopted} PDF(s); unescaped {unescaped} note(s); "
        f"re-tagged {retagged} note(s) read/unread; "
        f"reordered {reordered} frontmatter block(s).",
        fg=typer.colors.GREEN,
    )
    if bibfields:
        typer.secho(
            f"Filled bibliographic fields on {enriched} note(s).",
            fg=typer.colors.GREEN,
        )
    orphans = sorted(
        set(on_disk)
        - {
            str(vault.read_frontmatter(p).get("cite_key") or "")
            for p in papers_dir.glob("*.md")
        }
    )
    if orphans:
        typer.secho(
            f"{len(orphans)} PDF(s) match no note: {', '.join(orphans[:5])}",
            fg=typer.colors.YELLOW,
        )


def _load(papers_dir: Path) -> list[search.Record]:
    try:
        return search.load(papers_dir)
    except search.SearchError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


def _resolve(records: Sequence[search.Record], target: str) -> search.Record:
    try:
        return search.resolve(records, target)
    except search.SearchError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


def _citations(record: search.Record) -> str:
    """``n.d. cit`` for an unrecorded count, which is not the same as zero."""
    return f"{record.citations} cit" if record.citations is not None else "n.d. cit"


def _venue(record: search.Record, width: int = 46) -> str:
    """Venue names run to 100 characters in this vault; a line has to fit."""
    name = " ".join((record.venue or "unknown venue").split())
    return name if len(name) <= width else f"{name[: width - 1]}…"


def _headline(record: search.Record) -> str:
    year = record.year if record.year is not None else "n.d."
    mark = "[pdf]" if record.has_pdf else "[no pdf]"
    return (
        f"{record.title} — {record.byline} · {_venue(record)} {year} · "
        f"{_citations(record)} · {mark}"
    )


def _as_dict(hit: search.Hit) -> dict[str, Any]:
    record = hit.record
    return {
        "cite_key": record.cite_key,
        "title": record.title,
        "authors": list(record.authors),
        "year": record.year,
        "venue": record.venue,
        "citations": record.citations,
        "doi": record.doi,
        "openalex_id": record.openalex_id,
        "topics": list(record.topics),
        "tags": list(record.tags),
        "note_path": str(record.path),
        "pdf_path": str(record.pdf_path) if record.pdf_path else None,
        "pdf_url": record.pdf_url,
        "score": round(hit.score, 4),
        "matched_field": hit.field or None,
        "snippet": hit.snippet or None,
    }


@app.command()
def find(
    query: Annotated[
        list[str] | None, typer.Argument(help="Words to search for; any may match")
    ] = None,
    topic: Annotated[
        str | None, typer.Option("--topic", help="Only papers under this topic hub")
    ] = None,
    tag: Annotated[
        str | None, typer.Option("--tag", help="Only papers with this tag")
    ] = None,
    venue: Annotated[
        str | None, typer.Option("--venue", help="Substring of the venue name")
    ] = None,
    min_year: Annotated[
        int | None, typer.Option("--min-year", help="Published on or after")
    ] = None,
    max_year: Annotated[
        int | None, typer.Option("--max-year", help="Published on or before")
    ] = None,
    min_citations: Annotated[
        int | None, typer.Option("--min-citations", help="At least this many citations")
    ] = None,
    has_pdf: Annotated[
        bool | None,
        typer.Option(
            "--has-pdf/--no-pdf", help="Only papers whose PDF is (not) on disk"
        ),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="How many hits to print")] = 10,
    full: Annotated[
        bool, typer.Option("--full", help="Print whole notes, not snippets")
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Machine-readable output")
    ] = False,
) -> None:
    """Search the research notes, best match first.

    Terms are OR-ed and ranked by BM25, so throwing synonyms in together widens
    the net rather than narrowing it: "intermittent batteryless transiently
    powered" finds papers using any of the three vocabularies.

    With filters but no query, lists the filtered set by citation count: the
    "show me the shelf" mode.
    """
    records = _load(_papers_dir())
    filtered = search.apply_filters(
        records,
        topic=topic,
        tag=tag,
        venue=venue,
        min_year=min_year,
        max_year=max_year,
        min_citations=min_citations,
        has_pdf=has_pdf,
    )

    text = " ".join(query or [])
    hits = (
        search.rank(filtered, text) if text.strip() else search.by_citations(filtered)
    )

    if as_json:
        typer.echo(json.dumps([_as_dict(hit) for hit in hits[:limit]], indent=2))
        return

    shown = hits[:limit]
    ordering = "" if text.strip() else "   (no query; ranked by citations)"
    typer.secho(
        f"{len(records)} notes · {len(hits)} matched · showing {len(shown)}{ordering}",
        fg=typer.colors.CYAN,
    )
    if not shown:
        typer.secho(
            "Nothing matched. Try the field's other vocabulary before concluding "
            "the vault is thin here.",
            fg=typer.colors.YELLOW,
        )
        return
    typer.echo("")

    for position, hit in enumerate(shown, start=1):
        record = hit.record
        typer.echo(f"{position:>2}. {_headline(record)}")
        typer.secho(f"    {record.cite_key}", fg=typer.colors.BRIGHT_BLACK)
        if record.topics:
            typer.echo(_indent(f"topics: {' · '.join(record.topics)}"))
        if full:
            for name in (*vault.BODY_SECTIONS,):
                if content := record.sections.get(name):
                    typer.echo(f"    ## {name}")
                    typer.echo(_indent(content, "    "))
            if record.pdf_path:
                typer.echo(f"    pdf: {record.pdf_path}")
        elif hit.snippet:
            typer.echo(_indent(f"{hit.field}: {hit.snippet}", "    "))
        typer.echo("")

    if len(hits) > len(shown):
        typer.secho(
            f"{len(hits) - len(shown)} more; re-run with --limit {len(hits)}",
            fg=typer.colors.BRIGHT_BLACK,
        )


def _indent(text: str, prefix: str = "    ", width: int = 88) -> str:
    """Wrap and indent, so a snippet stays visibly inside its own entry."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        if current and len(prefix) + len(current) + 1 + len(word) > width:
            lines.append(prefix + current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    if current:
        lines.append(prefix + current)
    return "\n".join(lines)


@app.command()
def show(
    targets: Annotated[list[str], typer.Argument(help="Cite keys, note names or DOIs")],
) -> None:
    """Print whole notes, resolved by cite key, note name or DOI.

    The drill-down after ``find``: note filenames are titles, so they are long
    and full of punctuation a shell argues with.
    """
    records = _load(_papers_dir())
    for target in targets:
        record = _resolve(records, target)
        typer.secho(
            f"── {record.path.name} " + "─" * max(0, 60 - len(record.path.name)),
            fg=typer.colors.CYAN,
        )
        typer.echo(f"{record.title} · {', '.join(record.authors)}")
        year = record.year if record.year is not None else "n.d."
        typer.echo(f"{_venue(record, 88)} {year} · {_citations(record)}")
        bits = [record.cite_key]
        if record.doi:
            bits.append(f"doi:{record.doi}")
        if record.openalex_id:
            bits.append(record.openalex_id)
        if record.open_access:
            bits.append(f"{record.open_access} OA")
        typer.echo(" · ".join(bits))
        if record.topics:
            typer.echo(f"topics: {' · '.join(record.topics)}")
        if record.tags:
            typer.echo(f"tags: {', '.join(record.tags)}")
        # The path, not just a tick: this is what makes the PDF readable next.
        typer.echo(f"pdf: {record.pdf_path or record.pdf_url or 'none'}")
        typer.echo(f"note: {record.path}")

        for name in vault.BODY_SECTIONS:
            typer.echo("")
            typer.secho(f"## {name}", fg=typer.colors.GREEN)
            content = record.sections.get(name, "").strip()
            # "(empty)" is a fact about the note, not a failure to find one.
            typer.echo(content if content else "  (empty)")
        typer.echo("")


# \cite, \parencite, \autocite, \textcite, \footcite, \supercite, starred or
# not, with any number of optional [prenote][postnote] arguments before the key
# list. The keys themselves are one capture, split on commas by the caller.
_CITE = re.compile(
    r"\\(?:no|foot|super|auto|paren|text|smart|full)?cite[a-zA-Z]*\*?"
    r"(?:\[[^\]]*\])*\{([^}]*)\}"
)
# A .bib entry key, for --bib.
_BIB_ENTRY = re.compile(r"^\s*@[a-zA-Z]+\s*\{\s*([^,\s]+)\s*,", re.MULTILINE)


def cited_keys(text: str, *, bib: bool = False) -> set[str]:
    """Every cite key a LaTeX document cites, or a ``.bib`` file defines."""
    if bib:
        return {match.group(1).strip() for match in _BIB_ENTRY.finditer(text)}
    return {
        key.strip()
        for match in _CITE.finditer(text)
        for key in match.group(1).split(",")
        if key.strip()
    }


@app.command(name="cite-check")
def cite_check(
    files: Annotated[
        list[Path], typer.Argument(help="LaTeX documents (or .bib files with --bib)")
    ],
    bib: Annotated[
        bool, typer.Option("--bib", help="Read .bib entry keys instead of \\cite")
    ] = False,
    unused: Annotated[
        bool, typer.Option("--unused/--no-unused", help="Also list notes cited nowhere")
    ] = True,
) -> None:
    """Reconcile the cite keys in a document against the notes in the vault.

    The one command that connects the vault to what is being written. A key
    cited with no note behind it is the real error, and the exit code says so; a
    note cited nowhere is only the gap between a reading list and an argument.
    """
    records = _load(_papers_dir())
    known = {record.cite_key: record for record in records}

    wanted: set[str] = set()
    for path in files:
        try:
            wanted |= cited_keys(path.read_text(encoding="utf-8"), bib=bib)
        except OSError as exc:
            typer.secho(f"{path}: {exc.strerror}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc

    missing = sorted(wanted - set(known))
    typer.secho(
        f"{len(wanted)} cite key(s) in {len(files)} file(s) · "
        f"{len(records)} notes · {len(missing)} with no note",
        fg=typer.colors.CYAN,
    )
    for key in missing:
        typer.secho(f"  {key}", fg=typer.colors.RED)

    if unused:
        # Not an error: a vault is meant to be larger than any one paper.
        uncited = sorted(set(known) - wanted)
        typer.secho(
            f"\n{len(uncited)} note(s) cited nowhere", fg=typer.colors.BRIGHT_BLACK
        )
        for key in uncited[:10]:
            typer.secho(f"  {key}", fg=typer.colors.BRIGHT_BLACK)
        if len(uncited) > 10:
            typer.secho(
                f"  … and {len(uncited) - 10} more", fg=typer.colors.BRIGHT_BLACK
            )

    if missing:
        raise typer.Exit(1)


@app.command()
def near(
    target: Annotated[str, typer.Argument(help="Cite key, note name or DOI")],
) -> None:
    """Papers this one cites, and papers in the vault citing it.

    The reverse direction is computed by scanning every note's ``cites``: there
    is deliberately no ``cited_by`` property, and Obsidian's "Linked mentions"
    pane is not reachable from a terminal.
    """
    records = _load(_papers_dir())
    record = _resolve(records, target)
    typer.secho(f"{record.title} ({record.cite_key})", fg=typer.colors.CYAN)

    cites, unresolved = search.cites_in_vault(records, record)
    typer.echo(f"\n  cites, in the vault ({len(cites)})")
    for other in sorted(cites, key=lambda r: -(r.year or 0)):
        typer.echo(f"    ← {other.title} · {other.byline} {other.year or 'n.d.'}")
    if not cites:
        typer.secho("    none", fg=typer.colors.BRIGHT_BLACK)

    citing = search.cited_by(records, record)
    typer.echo(f"\n  cited by, in the vault ({len(citing)})")
    for other in sorted(citing, key=lambda r: -(r.year or 0)):
        typer.echo(f"    → {other.title} · {other.byline} {other.year or 'n.d.'}")
    if not citing:
        typer.secho("    none", fg=typer.colors.BRIGHT_BLACK)

    # How much of this paper's bibliography `expand` has not pulled in yet.
    # `record.cites` already holds the unresolved names, so adding them again
    # would count every gap twice and understate the coverage.
    total = len(record.cites)
    typer.echo(f"\n  cites, not in the vault: {len(unresolved)} of {total} unresolved")


def _pdf_of(record: search.Record) -> Path:
    """The PDF backing a note, or a diagnosis of why there is not one."""
    if record.pdf_path is None:
        hint = (
            f" Save it from a browser: {record.pdf_url}"
            if record.pdf_url
            else " No pdf_url recorded either."
        )
        typer.secho(
            f"{record.cite_key} has no PDF on disk.{hint}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    return record.pdf_path


def _extract(record: search.Record) -> list[highlights.Highlight]:
    """Read one note's PDF, reporting what had no recoverable text on stderr."""
    try:
        found, skipped = highlights.extract(_pdf_of(record))
    except highlights.HighlightError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    for reason, note in (
        (
            highlights.FREE_DRAW,
            "no text geometry; re-highlight by selection to capture them",
        ),
        (highlights.NO_TEXT, "selected no text; over a figure?"),
    ):
        pages = [s.page for s in skipped if s.reason == reason]
        if pages:
            where = ", ".join(f"p{page}" for page in pages)
            typer.secho(
                f"skipped {len(pages)} {reason} highlight(s) ({where}): {note}",
                fg=typer.colors.YELLOW,
                err=True,
            )
    return found


def _notes_section(record: search.Record) -> str:
    """The note's ``## Notes`` prose. ``tidy`` guarantees the heading exists."""
    if "Notes" not in record.sections:
        typer.secho(
            f"{record.cite_key}: the note has no `## Notes` heading. "
            "Run `tidy` first; this will not synthesise the section.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    return record.sections["Notes"]


def _placed(record: search.Record) -> dict[tuple[int, str], str]:
    """Which heading each quote already in the note sits under.

    This is what makes a re-run sticky: a quote that is already grouped comes
    back carrying its heading, so stage 2 only has to place the new ones.
    """
    _, groups = highlights.split_notes(record.sections.get("Notes", ""))
    return {
        (quote.page, quote.text): heading
        for heading, quotes in groups
        for quote in quotes
    }


@app.command(name="highlights")
def highlight(
    target: Annotated[
        str | None, typer.Argument(help="Cite key, note name or DOI")
    ] = None,
    apply: Annotated[
        Path | None,
        typer.Option(
            "--apply",
            help="Write the note from a {heading: [order, ...]} JSON grouping",
        ),
    ] = None,
    every: Annotated[
        bool,
        typer.Option("--all", help="Count highlights in every note with a PDF"),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", help="Print what --apply would write, without writing it"
        ),
    ] = False,
    gold: Annotated[
        bool,
        typer.Option("--gold", help="Print the grouping the note already holds"),
    ] = False,
    score: Annotated[
        Path | None,
        typer.Option("--score", help="Score a grouping file against the note's own"),
    ] = None,
    regroup: Annotated[
        bool,
        typer.Option(
            "--regroup",
            help="Allow --apply to move quotes the note has already grouped",
        ),
    ] = False,
    audit: Annotated[
        bool,
        typer.Option("--audit", help="Re-derive every quote and check the notes match"),
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Machine-readable output for --all/--audit")
    ] = False,
) -> None:
    """Recover the text under a PDF's highlights, verbatim.

    The default output is JSON on stdout: one object per highlight, in reading
    order, plus the heading it already sits under in the note. Nothing here has
    a model in it, and no rule touches the case or punctuation of a quote.
    """
    papers_dir = _papers_dir()
    records = _load(papers_dir)

    if audit:
        _audit_highlights(records, as_json=as_json)
        return

    if every:
        _count_highlights(records, as_json=as_json)
        return

    if target is None:
        typer.secho(
            "Give a cite key, or --all, or --audit.", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(1)

    record = _resolve(records, target)
    found = _extract(record)

    if gold:
        typer.echo(
            json.dumps(_gold_grouping(record, found), indent=2, ensure_ascii=False)
        )
        return

    if score is not None:
        _score_grouping(record, found, score)
        return

    if apply is not None:
        _apply_grouping(record, found, apply, regroup=regroup, dry_run=dry_run)
        return

    placed = _placed(record)
    typer.echo(
        json.dumps(
            [
                {
                    "order": h.order,
                    "page": h.page,
                    "text": h.text,
                    "group": placed.get((h.page, h.text)),
                }
                for h in found
            ],
            indent=2,
            ensure_ascii=False,
        )
    )


def _load_grouping(path: Path) -> dict[str, list[int]]:
    """A ``{heading: [order, ...]}`` file, as written by the skill."""
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        typer.secho(
            f"{path}: expected an object of {{heading: [order, ...]}}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    return {str(k): [int(o) for o in v] for k, v in loaded.items()}


def _gold_grouping(
    record: search.Record, found: Sequence[highlights.Highlight]
) -> dict[str, list[int]]:
    """The grouping the note already holds, the one you approved by keeping it."""
    _, groups = highlights.split_notes(record.sections.get("Notes", ""))
    grouping, unresolved = highlights.grouping_of(found, groups)
    if unresolved:
        typer.secho(
            f"left out {len(unresolved)} quote(s) the PDF no longer produces; "
            "run --audit",
            fg=typer.colors.YELLOW,
            err=True,
        )
    return grouping


def _score_grouping(
    record: search.Record, found: Sequence[highlights.Highlight], path: Path
) -> None:
    """Report how far a candidate grouping is from the note's own."""
    candidate = _load_grouping(path)
    try:
        scores = highlights.score_groupings(_gold_grouping(record, found), candidate)
    except highlights.HighlightError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.secho(f"{path.name} against {record.cite_key}", fg=typer.colors.CYAN)
    for label, value in (
        ("adjusted Rand", scores.adjusted_rand),
        ("homogeneity", scores.homogeneity),
        ("completeness", scores.completeness),
        ("V-measure", scores.v_measure),
    ):
        typer.echo(f"    {label:<16}{value:>7.3f}")


def _apply_grouping(
    record: search.Record,
    found: Sequence[highlights.Highlight],
    path: Path,
    *,
    regroup: bool,
    dry_run: bool,
) -> None:
    """Write the machine-owned region of ``## Notes`` from a grouping by order."""
    grouping = _load_grouping(path)
    # The same lookup stage 1 reports as ``group``, keyed the way the grouping
    # file addresses a quote. ``--regroup`` withholds it, which is what makes a
    # wholesale rewrite an explicit act rather than an accident.
    placed = _placed(record)
    by_order = {
        h.order: heading
        for h in found
        if (heading := placed.get((h.page, h.text))) is not None
    }
    try:
        groups = highlights.group_quotes(
            found, grouping, placed=None if regroup else by_order
        )
    except highlights.HighlightError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    prose, _ = highlights.split_notes(_notes_section(record))
    sections = dict(record.sections)
    # The file's own name, which is what an Obsidian link resolves by.
    sections["Notes"] = highlights.render_notes(
        prose, groups, pdf_name=_pdf_of(record).name
    )
    if dry_run:
        typer.echo(sections["Notes"])
        return
    changed = vault.write_body(record.path, vault.render_body(sections))
    vault.sync_read_tag(record.path, sections)
    count = sum(len(quotes) for _, quotes in groups)
    typer.secho(
        f"{'Wrote' if changed else 'Unchanged:'} {count} quote(s) under "
        f"{len(groups)} heading(s): {record.path}",
        fg=typer.colors.GREEN if changed else typer.colors.BRIGHT_BLACK,
    )


def _count_highlights(records: Sequence[search.Record], *, as_json: bool) -> None:
    counts: dict[str, int] = {}
    for record in records:
        if record.pdf_path is None:
            continue
        try:
            found, _ = highlights.extract(record.pdf_path)
        except highlights.HighlightError as exc:
            typer.secho(str(exc), fg=typer.colors.YELLOW, err=True)
            continue
        if found:
            counts[record.cite_key] = len(found)
    if as_json:
        typer.echo(json.dumps(counts, indent=2))
        return
    with_pdf = sum(1 for r in records if r.pdf_path is not None)
    typer.secho(
        f"{with_pdf} PDF(s) \u00b7 {len(counts)} highlighted \u00b7 "
        f"{sum(counts.values())} highlight(s)",
        fg=typer.colors.CYAN,
    )
    for key, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        typer.echo(f"    {key:<40}{count:>4}")


def _audit_highlights(records: Sequence[search.Record], *, as_json: bool) -> None:
    """Confirm every quote in the vault is byte-identical to the extractor's.

    A quote in a note that the PDF no longer produces is drift: either the
    highlight was deleted, or the text was hand-edited. Neither is repaired
    automatically, because the edit may have been deliberate.
    """
    drifted: list[dict[str, Any]] = []
    unwritten: dict[str, int] = {}
    checked = 0
    for record in records:
        if record.pdf_path is None or "Notes" not in record.sections:
            continue
        _, groups = highlights.split_notes(record.sections["Notes"])
        try:
            found, _ = highlights.extract(record.pdf_path)
        except highlights.HighlightError as exc:
            typer.secho(str(exc), fg=typer.colors.YELLOW, err=True)
            continue
        derived = {(h.page, h.text) for h in found}
        in_note = {(q.page, q.text) for _, quotes in groups for q in quotes}
        for page, text in sorted(in_note - derived):
            # The nearest quote on the same page is what it was probably edited
            # from, and showing both is what makes the report actionable.
            same_page = [h.text for h in found if h.page == page]
            nearest = min(
                same_page,
                key=lambda candidate: _distance(candidate, text),
                default=None,
            )
            drifted.append(
                {
                    "cite_key": record.cite_key,
                    "page": page,
                    "in_note": text,
                    "extracted": nearest,
                }
            )
        checked += len(in_note)
        if missing := len(derived - in_note):
            unwritten[record.cite_key] = missing

    if as_json:
        typer.echo(
            json.dumps(
                {"checked": checked, "drifted": drifted, "unwritten": unwritten},
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        typer.secho(
            f"{checked} quote(s) in the vault \u00b7 {len(drifted)} disagreeing with "
            f"the PDF",
            fg=typer.colors.GREEN if not drifted else typer.colors.RED,
        )
        for entry in drifted:
            typer.echo(f"\n  {entry['cite_key']} p{entry['page']}")
            typer.secho(f"    note: {entry['in_note']}", fg=typer.colors.YELLOW)
            typer.secho(f"    pdf:  {entry['extracted']}", fg=typer.colors.CYAN)
        if unwritten:
            total = sum(unwritten.values())
            names = ", ".join(sorted(unwritten)[:5])
            typer.secho(
                f"{total} extracted highlight(s) not yet in a note: {names}",
                fg=typer.colors.BRIGHT_BLACK,
            )
    if drifted:
        raise typer.Exit(1)


def _distance(a: str, b: str) -> int:
    """How unalike two quotes are: enough to pick the one that was edited."""
    return len(set(a.split()) ^ set(b.split()))


@app.command()
def pdf(
    target: Annotated[
        str | None, typer.Argument(help="Cite key, note name or DOI")
    ] = None,
    note: Annotated[
        str | None,
        typer.Option("--note", help="Reverse: the note owning this PDF filename"),
    ] = None,
    audit: Annotated[
        bool, typer.Option("--audit", help="Reconcile the notes against the PDF folder")
    ] = False,
) -> None:
    """The note↔PDF join, both directions.

    The forward form prints the absolute path and nothing else, so it pipes and
    can be handed straight to a reader.
    """
    papers_dir = _papers_dir()
    records = _load(papers_dir)

    if audit:
        _print_audit(records, pdfs_dir=search.pdfs_dir_for(papers_dir))
        return

    if note is not None:
        try:
            owner = search.resolve_pdf(records, note)
        except search.SearchError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc
        typer.echo(owner.title)
        typer.echo(
            f"{owner.cite_key} · {owner.byline} · {_venue(owner)} "
            f"{owner.year or 'n.d.'}"
        )
        typer.echo(f"note: {owner.path}")
        return

    if target is None:
        typer.secho(
            "Give a cite key, or --note <file.pdf>, or --audit.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    record = _resolve(records, target)
    if record.pdf_path is None:
        hint = (
            f" Save it from a browser: {record.pdf_url}"
            if record.pdf_url
            else " No pdf_url recorded either."
        )
        # stderr, so the stdout contract stays "a path or nothing".
        typer.secho(
            f"{record.cite_key} has no PDF on disk.{hint}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    typer.echo(str(record.pdf_path))


def _print_audit(records: Sequence[search.Record], *, pdfs_dir: Path) -> None:
    report = search.audit(records, pdfs_dir=pdfs_dir)
    share = round(100 * report.with_pdf / report.total) if report.total else 0
    without = report.total - report.with_pdf
    typer.secho(
        f"{report.total} notes · {report.with_pdf} with a PDF ({share}%) · "
        f"{without} without",
        fg=typer.colors.CYAN,
    )
    for label, names in (
        ("orphan PDFs (no note claims them)", report.orphans),
        ("notes claiming a PDF that is not on disk", report.missing),
        ("PDFs on disk that no note claims", report.unclaimed),
    ):
        colour = typer.colors.GREEN if not names else typer.colors.YELLOW
        typer.secho(f"{label + ':':<42}{len(names)}", fg=colour)
        if names:
            typer.echo(f"    {', '.join(names[:8])}")
    colour = typer.colors.GREEN if not report.mismatched else typer.colors.YELLOW
    typer.secho(
        f"{'pdf link disagreeing with cite_key:':<42}{len(report.mismatched)}",
        fg=colour,
    )
    for key, claimed in report.mismatched[:8]:
        typer.echo(f"    {key} claims {claimed}")
    typer.echo(
        f"of the {without} without: {report.without_pdf_with_url} have a pdf_url "
        f"recorded, {report.without_pdf_no_url} have none"
    )


if __name__ == "__main__":
    app()
