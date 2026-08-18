"""``just paper`` and ``just bib`` — deterministic reference management."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import httpx
import typer
from dotenv import load_dotenv

from earth_computers.refs import bibtex, notion, sources

load_dotenv()

app = typer.Typer(add_completion=False, help=__doc__)

DEFAULT_BIB = Path("thesis/refs.bib")


@app.command()
def paper(
    doi: Annotated[str, typer.Argument(help="DOI, bare or as a doi.org URL")],
    force: Annotated[
        bool, typer.Option("--force", help="Add even if the DOI is already present")
    ] = False,
) -> None:
    """Look up a DOI and add it to the Notion Research Resources database."""
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

        try:
            if not force and record.doi:
                existing = notion.find_by_doi(record.doi, client=client)
                if existing is not None:
                    typer.secho(
                        "Already in Notion — use --force to add anyway.",
                        fg=typer.colors.YELLOW,
                    )
                    raise typer.Exit(0)
            url = notion.create_paper(record, client=client)
        except notion.NotionError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc

    typer.secho(f"Added: {url}", fg=typer.colors.GREEN)
    typer.echo("Set Relevance, Topics, Section and Key Takeaway in Notion.")


@app.command()
def bib(
    out: Annotated[Path, typer.Option("--out", help="Output path")] = DEFAULT_BIB,
) -> None:
    """Regenerate ``thesis/refs.bib`` from the Notion database."""
    with httpx.Client(follow_redirects=True) as client:
        try:
            entries = notion.fetch_all(client=client)
        except notion.NotionError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(bibtex.render(entries), encoding="utf-8")
    typer.secho(f"Wrote {len(entries)} entries to {out}", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
