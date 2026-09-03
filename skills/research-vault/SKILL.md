---
name: research-vault
description: Use when answering any literature question against an Obsidian bibliography vault — "do we have anything on X", "what should I cite for Y", prior-art checks, related-work drafting, picking papers to read — or when touching the paper notes, their PDFs, or a generated .bib. Covers ranked search over the vault, note↔PDF navigation, and what the notes do and do not record.
---

# The research vault

One Markdown note per paper, PDFs in a sibling `pdfs/`, topic hubs in a sibling
`topics/`. `VAULT_PAPERS_DIR` points at the notes; the bibliography is generated
from them.

Inside a uv project that depends on the package, prefix every command with `uv
run`; the project may also wrap them in `just` recipes.

## Search it, do not read it

**Never answer a literature question by `Read`ing the papers folder.** One note
is a couple of kilobytes and a corpus is hundreds of them; reading three notes
and generalising is the failure mode these commands exist to replace.

| Command | Gives you |
|---|---|
| `find "<query>"` | Ranked hits, best match first |
| `show <key>` | One whole note |
| `near <key>` | Graph neighbours: cited, and citing |
| `pdf <key>` | An absolute path on stdout, nothing else |

Ranking is BM25 over title (×3), topics (×2), and abstract, notes, venue and
authors (×1). Deterministic, no index, no model. These four never write.

## Query technique

| Property | Consequence |
|---|---|
| Terms are **OR-ed** | Throwing synonyms in together widens the net: `find "intermittent batteryless transiently powered"` |
| **No stemmer** | `backscatter` does not match `backscattering`. If a query looks thin, re-run with the field's own vocabulary before concluding anything. |
| Filters apply **before** ranking | `find "backscatter tag" --topic "Energy Harvesting"` |
| Filters with **no query** | Lists the filtered set by citation count — the "show me the shelf" mode |

Filters: `--topic`, `--tag`, `--venue`, `--min-year`, `--max-year`,
`--min-citations`, `--has-pdf` / `--no-pdf`, `--limit`, `--full`, `--json`.

**On an ambiguous term, filter first.** A word meaning two things in two fields
cannot be separated by ranking, because both senses match it equally well —
and `expand` harvests along the citation graph, so a whole cluster from the
wrong field can arrive on one shared word.

Every result line ends in `[pdf]` or `[no pdf]`, and the header says how much of
the corpus was considered (`177 notes · 12 matched · showing 5`). **A thin
result is a finding about the literature**; report it as one rather than padding.

## PDFs: usually a minority of notes

`pdf` prints a path and nothing else, so it pipes and can be handed straight to
`Read` — which opens PDFs by page range. That is the whole chain from a question
to the actual paper:

```sh
research-assistant find "tunnel diode" --has-pdf   # readable past the abstract
research-assistant pdf varshney2019tunnelscatter   # -> the path; then Read it
research-assistant pdf --note maioli2021alfred.pdf # reverse: which note owns it
research-assistant pdf --audit                     # reconcile notes vs. folder
```

`--no-pdf` is the other half: the reading list of what still has to be obtained.

**Say which you are working from.** When a note has no PDF, the claim rests on
the abstract alone — never imply the full text was consulted. Run `pdf --audit`
before making a coverage claim.

## What the vault does and does not know

| | |
|---|---|
| `## Notes` | Empty unless somebody wrote it or ran `highlights`. Most corpora are publisher abstracts, not opinions. `find` answers "what is in this literature", **never** "what did you think of it" — do not present one as the other. |
| Frontmatter | **Intrinsic properties only.** Do not propose adding a relevance score or a "read yet?" field to improve search; that judgement belongs in prose. See `docs/note-format.md` in the research-assistant repo. |
| `topics` | Wikilinks to hub notes in `topics/`. A topic earns a hub only once several papers share it. |
| `near` | Derives "cited by" by scanning every note's `cites` — there is deliberately no `cited_by`. Its unresolved count says how much of a paper's bibliography `expand` has not pulled in. |
| Titles | Sometimes truncated upstream — Crossref records Mementos as just "Mementos". Check the PDF before quoting a title. |

## The write side

Notes are write-once and Obsidian owns them.

| Command | Writes |
|---|---|
| `paper <doi\|openalex-id>` | One new note |
| `source --title … --author …` | One note for what no index knows |
| `expand` | Notes one hop out along the citation graph |
| `relink` | `cites`, `topics`, and the hub notes |
| `tidy` | Note bodies, missing abstracts, the `read` tag |
| `bib --out <file>` | The bibliography |
