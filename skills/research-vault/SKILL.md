---
name: research-vault
description: Use when answering any literature question against an Obsidian bibliography vault — "do we have anything on X", "what should I cite for Y", prior-art checks, related-work drafting, picking papers to read — or when touching the paper notes, their PDFs, or a generated .bib. Covers ranked search over the vault, note↔PDF navigation, and what the notes do and do not record.
---

# The research vault

One Markdown note per paper, PDFs in a sibling `pdfs/`, topic hubs in a sibling
`topics/`. `VAULT_PAPERS_DIR` points at the notes. The notes are the source of
truth for the bibliography, which is generated from them.

Inside a uv project that depends on the package, prefix every command below with
`uv run`; the project may also wrap them in `just` recipes.

## Search it, do not read it

**Never answer a literature question by `Read`ing the papers folder.** One note
is a couple of kilobytes and a corpus is hundreds of them; reading three notes
and generalising is the failure mode these commands exist to replace.

```sh
research-assistant find "intermittent computing checkpointing"  # ranked
research-assistant show maioli2021alfred                        # one whole note
research-assistant near maioli2021alfred                        # graph neighbours
research-assistant pdf maioli2021alfred                         # -> absolute path
```

Ranking is BM25 over title (×3), topics (×2), the hand-written takeaway (×2),
and abstract, notes, venue and authors (×1). Deterministic, no index, no model.

## Query technique

Terms are **OR-ed**, so throwing synonyms in together widens the net:

```sh
research-assistant find "intermittent batteryless transiently powered"
```

There is no stemmer. "backscatter" does not match "backscattering" — if a query
looks thin, re-run it with the field's own vocabulary before concluding
anything.

**On an ambiguous term, filter first.** A word that means two things in two
fields cannot be separated by ranking, because both senses match it equally
well. `expand` harvests along the citation graph, so a whole cluster from the
wrong field can arrive on one shared word:

```sh
research-assistant find "backscatter tag" --topic "Energy Harvesting"
```

Filters: `--topic`, `--tag`, `--venue`, `--min-year`, `--max-year`,
`--min-citations`, `--has-pdf` / `--no-pdf`, `--limit`, `--full`, `--json`.
They apply before ranking. With filters but **no query**, `find` lists the
filtered set by citation count — the "show me the shelf" mode.

Every result line ends in `[pdf]` or `[no pdf]`, and the header says how much of
the corpus was considered (`177 notes · 12 matched · showing 5`). A thin result
is a finding about the literature; report it as one rather than padding.

## PDFs: usually a minority of notes

`research-assistant pdf <key>` prints an absolute path and nothing else on
stdout, so it pipes and can be handed straight to `Read` — which opens PDFs by
page range. That is the whole chain from a question to the actual paper:

```sh
research-assistant find "tunnel diode" --has-pdf   # readable past the abstract
research-assistant pdf varshney2019tunnelscatter   # -> the path; then Read it
research-assistant pdf --note maioli2021alfred.pdf # reverse: which note owns it
research-assistant pdf --audit                     # reconcile notes vs. folder
```

`--no-pdf` is the other half: the reading list of what still has to be obtained.

**Say which you are working from.** When a note has no PDF, the claim rests on
the abstract alone — never imply the full text was consulted. Run
`pdf --audit` before making a coverage claim.

## What the vault does and does not know

- `## Key takeaway` is empty unless somebody wrote it. Most corpora are
  publisher abstracts, not opinions. `find` answers "what is in this
  literature", never "what did you think of it" — do not present one as the
  other.
- Frontmatter holds only **intrinsic** properties. Do not propose adding a
  relevance score or a "read yet?" field to improve search; that judgement
  belongs in prose. See `docs/note-format.md` in the research-assistant repo.
- `topics` are wikilinks to hub notes in `topics/`; a topic earns a hub only
  once several papers share it.
- `near` derives "cited by" by scanning every note's `cites`: there is
  deliberately no `cited_by` property. Its unresolved count says how much of a
  paper's bibliography `expand` has not pulled in.
- Titles are sometimes truncated upstream — Crossref records Mementos as just
  "Mementos". Check the PDF before quoting a title.

## These commands are read-only

Notes are write-once and Obsidian owns them. `find`, `show`, `near` and `pdf`
never write. The write side: `paper <doi>` adds one, `source` records what no
index knows, `expand` walks the citation graph, `relink` rebuilds links and
hubs, `tidy` reformats bodies and adopts hand-saved PDFs, `bib --out <file>`
regenerates the bibliography.
