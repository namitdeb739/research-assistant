"""``just paper`` and ``just bib`` — deterministic reference management."""

from __future__ import annotations

import collections
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import httpx
import typer
from dotenv import load_dotenv

from earth_computers.config import Config
from earth_computers.refs import bibtex, graph, sources, vault

if TYPE_CHECKING:
    from collections.abc import Sequence

    from earth_computers.refs.models import Paper

load_dotenv()

app = typer.Typer(add_completion=False, help=__doc__)

DEFAULT_BIB = Path("thesis/refs.bib")


def _papers_dir() -> Path:
    """The vault folder holding one note per paper."""
    return Path(Config().vault_papers_dir)


@app.command()
def paper(
    doi: Annotated[str, typer.Argument(help="DOI, bare or as a doi.org URL")],
    force: Annotated[
        bool, typer.Option("--force", help="Add even if the DOI is already present")
    ] = False,
) -> None:
    """Look up a DOI and add it to the Obsidian Research Resources notes."""
    with httpx.Client(follow_redirects=True) as client:
        try:
            record = sources.resolve(doi, client=client)
        except sources.DoiLookupError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc

        typer.echo(f"  {record.title}")
        if record.authors:
            shown = ", ".join(record.authors[:3])
            suffix = " et al." if len(record.authors) > 3 else ""
            typer.echo(f"  {shown}{suffix}")
        typer.echo(f"  {record.venue or 'unknown venue'} ({record.year or 'n.d.'})")

        papers_dir = _papers_dir()
        key = record.cite_key()
        try:
            if not force and record.doi:
                existing = vault.find_by_doi(record.doi, papers_dir=papers_dir)
                if existing is not None:
                    typer.secho(
                        f"Already in the vault at {existing} — "
                        "use --force to add anyway.",
                        fg=typer.colors.YELLOW,
                    )
                    raise typer.Exit(0)

            pdf_name = _save_pdf(record, key, papers_dir=papers_dir, client=client)
            path = vault.create_paper(
                record, key, papers_dir=papers_dir, pdf_name=pdf_name
            )
        except vault.VaultError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc

    typer.secho(f"Added: {path}", fg=typer.colors.GREEN)
    typer.echo("Write the key takeaway and topics in Obsidian.")


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


@app.command()
def bib(
    out: Annotated[Path, typer.Option("--out", help="Output path")] = DEFAULT_BIB,
) -> None:
    """Regenerate ``thesis/refs.bib`` from the Obsidian vault."""
    try:
        entries = vault.read_all(papers_dir=_papers_dir())
    except vault.VaultError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(bibtex.render(entries), encoding="utf-8")
    typer.secho(f"Wrote {len(entries)} entries to {out}", fg=typer.colors.GREEN)


HARVESTED_TAG = "harvested"
SEED_TAG = "seed"


def _tags_for(subfields: Sequence[str], *, harvested: bool) -> list[str]:
    """``paper`` plus provenance plus one nested tag per OpenAlex subfield."""
    tags = ["paper", HARVESTED_TAG if harvested else SEED_TAG]
    tags.extend(f"topic/{slug}" for slug in subfields)
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
        fresh = {
            ident: candidate
            for ident, candidate in candidates.items()
            if ident not in by_openalex
            and not (candidate.doi and candidate.doi in by_doi)
        }
        typer.echo(
            f"{len(candidates)} candidate(s), {len(fresh)} not yet in the vault."
        )
        if not fresh:
            raise typer.Exit(0)

        works = {
            graph.bare_id(str(work.get("id", ""))): work
            for work in graph.fetch_works(sorted(fresh), client=client)
        }
        selected = _select(fresh, works, by_doi, min_year=min_year, limit=limit)

        if dry_run:
            _print_candidates(selected, works)
            typer.secho(
                f"\nDry run: {len(selected)} note(s) would be written.",
                fg=typer.colors.YELLOW,
            )
            raise typer.Exit(0)

        written, no_pdf = _write_notes(
            selected, works, papers_dir=papers_dir, client=client, pdfs=pdfs
        )

    for path, front in roots:
        subfields = _subfields_of(front)
        vault.update_frontmatter(path, {"tags": _tags_for(subfields, harvested=False)})

    typer.secho(
        f"\nWrote {len(written)} note(s) to {papers_dir}", fg=typer.colors.GREEN
    )
    if no_pdf:
        typer.secho(
            f"{len(no_pdf)} had no fetchable PDF — see the 'Missing PDF' view.",
            fg=typer.colors.YELLOW,
        )
    typer.echo("Now run: just relink")


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
    *,
    min_year: int | None,
    limit: int | None,
) -> list[graph.Candidate]:
    """Apply the year filter, drop works already present under a DOI, then cap."""
    kept: list[graph.Candidate] = []
    for ident, candidate in fresh.items():
        work = works.get(ident)
        if work is None:
            continue  # merged or deleted in OpenAlex
        doi = _work_doi(work)
        if doi and doi in by_doi:
            continue  # the candidate carried no DOI but the fetched work does
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
    return kept[:limit] if limit is not None else kept


def _print_candidates(
    selected: Sequence[graph.Candidate], works: dict[str, dict[str, Any]]
) -> None:
    for candidate in selected:
        work = works[candidate.openalex_id]
        year = work.get("publication_year") or "n.d."
        routes = ",".join(sorted(candidate.provenance))
        title = " ".join(str(work.get("title") or "Untitled").split())
        typer.echo(f"  {year}  [{routes}]  {title[:88]}")


def _write_notes(
    selected: Sequence[graph.Candidate],
    works: dict[str, dict[str, Any]],
    *,
    papers_dir: Path,
    client: httpx.Client,
    pdfs: bool,
) -> tuple[list[Path], list[str]]:
    """Resolve each candidate against Crossref and write its note."""
    written: list[Path] = []
    no_pdf: list[str] = []
    seen_keys: set[str] = set()

    for position, candidate in enumerate(selected, start=1):
        work = works[candidate.openalex_id]
        doi = _work_doi(work)
        record: Paper | None = None
        if doi:
            try:
                record = sources.resolve(doi, client=client)
            except sources.DoiLookupError, httpx.HTTPError:
                record = None
        if record is None:
            # Crossref does not know it, or has no DOI to know it by.
            record = sources.paper_from_openalex(work)

        key = record.cite_key()
        while key in seen_keys:
            key = f"{key}a"  # two papers, same author, year and first word
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
        written.append(path)
        typer.echo(f"  [{position}/{len(selected)}] {path.name}")
    return written, no_pdf


@app.command()
def relink() -> None:
    """Rebuild ``cites`` and ``topics`` across the vault from the citation graph.

    Only links to notes that actually exist are written, so the graph has no
    unresolved links. Obsidian derives the reverse direction itself in the
    "Linked mentions" pane, which is why there is no ``cited_by`` to keep in
    sync. Idempotent — safe to re-run after any expand.
    """
    papers_dir = _papers_dir()
    _, by_openalex = vault.index(papers_dir)
    if not by_openalex:
        typer.secho(
            "No note carries an openalex_id yet — run `just expand` first.",
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
        if vault.update_frontmatter(path, {"topics": linked, "cites": cites}):
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


@app.command()
def tidy(
    abstracts: Annotated[
        bool, typer.Option("--abstracts/--no-abstracts", help="Backfill from OpenAlex")
    ] = True,
) -> None:
    """Reformat note bodies to the template, and fill any abstract still missing.

    Notes were written by more than one code path over time and drifted into
    several whitespace shapes. This normalises them so a diff shows a changed
    sentence rather than a changed blank line. Section *content* is only ever
    reordered into the template, never rewritten, and headings the template does
    not know about are kept.
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

    reformatted = 0
    for path in sorted(papers_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        sections = vault.parse_body(vault._split_frontmatter(text)[1])
        if path in filled:
            sections["Abstract"] = filled[path]
        if vault.write_body(path, vault.render_body(sections)):
            reformatted += 1

    typer.secho(
        f"Reformatted {reformatted} note(s); filled {len(filled)} abstract(s).",
        fg=typer.colors.GREEN,
    )


if __name__ == "__main__":
    app()
