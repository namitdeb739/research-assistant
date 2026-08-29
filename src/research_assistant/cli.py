"""``just paper`` and ``just bib`` — deterministic reference management."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import httpx
import typer
from dotenv import load_dotenv

from earth_computers.config import Config
from earth_computers.refs import bibtex, sources, vault

if TYPE_CHECKING:
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


if __name__ == "__main__":
    app()
