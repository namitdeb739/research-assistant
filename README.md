# research-assistant

Deterministic reference management for an Obsidian vault: Crossref/OpenAlex →
one Markdown note per paper → BibTeX.

There is **no model in the loop**. Every command is a pure function of the two
public indexes and what is already on disk, so re-running one is idempotent and
two people running it get the same vault. Nothing is ever summarised, invented,
or paraphrased into your notes.

## The store

One Markdown note per paper in a folder of your choosing, open-access PDFs in a
sibling `pdfs/`, and topic hub notes in a sibling `topics/`. The notes are the
source of truth; the BibTeX file is generated from them.

Frontmatter holds only **intrinsic** properties — facts about the resource. A
judgement about it, or your progress through it, is prose under `## Key takeaway`
and `## Notes`, never a property: a five-point scale in a table is a worse record
of an opinion than a sentence is.

The note format, not the Python, is the public API. It is specified in
[`docs/note-format.md`](docs/note-format.md), and that is what the version number
is a promise about.

## Install

```bash
uv tool install git+https://github.com/namitdeb739/research-assistant
```

Inside a uv project that depends on it, the command is
`uv run research-assistant`.

## Configuration

Two environment variables, both optional as flags:

| | |
|---|---|
| `VAULT_PAPERS_DIR` | The vault folder holding one note per paper. Also `--papers-dir`. There is no default: guessing at someone's folder layout is worse than saying so. |
| `RESEARCH_ASSISTANT_USER_AGENT` | Your contact address, for the Crossref and OpenAlex polite pools — e.g. `myproject/1.0 (mailto:you@example.com)`. Unset means the anonymous pool, which is slower but works. |

## Commands

### Write

```bash
research-assistant paper 10.1145/3560905.3568538  # add one paper by DOI…
research-assistant paper W2300484078              # …or by OpenAlex id
research-assistant source --title "…" --author "…" --year 2026
research-assistant expand --dry-run               # walk the citation graph
research-assistant relink                         # rebuild links and topic hubs
research-assistant tidy                           # reformat bodies, fill abstracts
research-assistant bib --out refs.bib             # regenerate the bibliography
```

`paper` takes **a DOI or an OpenAlex id**, because a DOI is not universal —
USENIX mints none. A DOI goes to Crossref first, which is authoritative for the
fields a bibliography needs; an OpenAlex id falls back to that index alone.
Deduplication checks both keys.

`source` is the escape hatch below both indexes: it records what you type, with
no lookup at all. Crossref and OpenAlex cover journals and proceedings and
nothing else, but a bibliography cites more than that — a slide deck, a vendor
technical guide, a datasheet, a standard. Pass `--pdf` to file a local copy, and
`--key` when the derived cite key reads badly (a corporate author has no
surname). Such a note carries `doi: null` and `openalex_id: null`, so `expand`
and `relink` cannot reach it from the citation graph — link it by hand.

`expand` walks the graph one hop out from every note **not** tagged `harvested`,
so re-running it never quietly reaches depth 2. To go deeper, drop that tag from
the paper worth expanding.

`relink` rewrites `cites` as wikilinks to notes that actually exist, and writes
one topic hub note per theme. Links, not labels: a plain string groups a table
fine but is not a node, so it never reaches the graph. A topic earns a hub only
once several papers share it.

`tidy` owns the note *body*: it renders every note through one template, fills
any abstract the indexes have but the note lacks, and adopts any PDF in `pdfs/`
whose filename matches a cite key. Section content is only ever moved, never
rewritten, and headings the template does not know about are preserved.

### Read

```bash
research-assistant find "intermittent computing checkpointing"
research-assistant find "backscatter tag" --topic "Energy Harvesting"
research-assistant show maioli2021alfred
research-assistant near maioli2021alfred          # cited, and citing
research-assistant pdf maioli2021alfred           # -> absolute path
research-assistant highlights maioli2021alfred    # quotes under the highlights
```

`find` ranks by BM25 over title, topics, takeaway and abstract — no index, no
embeddings. `pdf` is the note↔PDF join in both directions, and `pdf --audit`
reconciles what the notes claim against what is on disk. The first four are
strictly read-only.

### Highlights

`highlights` recovers the text under the highlights you made in Obsidian's PDF
viewer, which the annotation dictionary stores as **geometry only**: it
intersects `/QuadPoints` with the page's characters, in quad-array order, because
that is pdf.js's selection order and therefore reading order even across columns.

**No quote is ever altered.** The only transformations are ligature expansion,
NFC, line-break de-hyphenation and whitespace collapsing, applied identically by
the writer and by `--audit` — so a typo in the source survives into your notes,
which is the point.

```bash
research-assistant highlights <key> > quotes.json   # verbatim, no model
research-assistant highlights <key> --apply groups.json
research-assistant highlights --audit               # re-derive every quote
```

`--apply` addresses quotes by their `order` number, never by their text, so the
write path is structurally incapable of rewriting one.

## Development

```bash
just setup   # .venv + deps + pre-commit hooks
just check   # lint + typecheck + test
just test-vault  # the tests that read a real vault
```

## Licence

MIT.
