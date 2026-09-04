# The screening ledger

`expand` screens and `promote` acquires. Before the ledger existed, a candidate
was filtered only against the notes currently present, so a paper you deleted
came back on the next run, and there was no record that you had looked at it and
said no.

A vault that grows by harvest and shrinks by deletion is a set of arrivals. The
ledger is what makes it a set of decisions, and it is the only thing that can
answer *how did you build this bibliography* in a methods chapter. It is also
now the store the reading list is rendered from, so the papers folder grows only
when you say `promote`.

## Where it lives, and why it is not frontmatter

```text
<papers-dir>/          the notes
../pdfs/
../topics/
../screening.tsv       ← here, a sibling
../Reading List.md     ← its pending rows, rendered
```

An exclusion is a fact about **your search process**, not an intrinsic property
of the paper. The note format bars judgements from frontmatter for exactly that
reason, so putting `excluded: true` on a note would break the rule that keeps
frontmatter worth having. And a paper you excluded has no note to carry the
property anyway.

It is visible rather than dotfiled on purpose: a decision log you cannot see is
one you will not trust.

## The file

```
# research-assistant screening v2
decided	decision	openalex_id	doi	year	citations	via	seeds	pdf_url	venue	authors	reason	title
2026-09-04T11:20:03+00:00	exclude	W2755950973	10.1145/3132211	2017	412	reference	W2300484078		ACM CSUR	Sudevalayam; Kulkarni	survey, not primary	A Survey of Energy Harvesting
```

Thirteen tab-separated columns, **no quoting**. The column order is the escaping
strategy: fixed-alphabet fields first, the free-text ones last, `title` last of
all. Tabs and newlines are flattened to single spaces on write, and on read any
extra tabs are rejoined into `title` — so a hand-edit can only ever damage the
human-readable column, never shift a decision into the wrong field.

Quoting would let a title carry a newline, and then the file stops being
greppable and `wc -l`-able, which is most of the point of a TSV. The ledger is
not the metadata store; the note is. `title` is a courtesy label.

| Column | Meaning |
|---|---|
| `decided` | Full UTC timestamp, so ordering is total and file order only breaks ties |
| `decision` | `include`, `exclude` or `pending` |
| `openalex_id` · `doi` | Either or both. A lookup tries the id first, then the DOI |
| `year` · `citations` | Integers, or empty when OpenAlex has none |
| `via` | How it was found: `reference`, `citation`, `related` |
| `seeds` | Which root notes led to it — provenance about the *decision* |
| `pdf_url` | The OA URL OpenAlex reported, so the list shows what is fetchable before you promote |
| `venue` · `authors` | Free text, `authors` joined with `; `. Enough to triage a candidate without a lookup |
| `reason` | Required for `exclude` |

### Two versions in one file

v1 rows were nine columns. There is no migration, because the module's headline
promise is that it has no rewrite path. **The version is sniffed per row, off
the field count:**

| Fields | Read as |
|---|---|
| exactly 9 | v1 |
| 10–12 | v1, with the excess rejoined into `title` — today's hand-edit tolerance, and a v2 writer never emits this many |
| ≥ 13 | v2, with the excess rejoined into `title` |

A writer always emits thirteen. A **new** file gets the v2 version line and
header; an existing v1 file keeps its own and simply gains v2 rows below them,
which is what append-only means. Reading a v1 row leaves the four triage columns
blank, and the reading list renders it with those cells empty.

## Append-only

That is a promise about the code, not an enforcement against your editor.
Nothing in `screening.py` opens the file for writing; there is no rewrite path
and no compaction. **A change of mind is a new row**, and reading folds by
*last row wins* — deliberately the opposite of how `vault.index` resolves
duplicates.

So if you delete a row by hand, it is as if the decision was never made; if you
add one, it counts. That is the correct property for a log you own.

## Using it

```sh
research-assistant expand --adopt              # seed from an existing vault
research-assistant expand --report             # the PRISMA numbers, writes nothing
research-assistant expand                      # candidates → pending rows
research-assistant reading-list                # re-render, and print the set
research-assistant promote W123                # pending → note, PDF, include row
research-assistant expand --exclude W123 --reason "survey, not primary"
research-assistant expand --include W123       # how you un-exclude
```

`--dry-run` still writes **nothing**, ledger rows included: a row is a write,
and so is a render.

`--limit` **defers rather than decides**. A paper cut by the cap was not judged,
so it gets no row and is offered again next time.

### The floor

One hop forward from ~180 seeds is thousands of works, so `expand` records only
the candidates clearing a bar:

```text
keep if (seeds reached >= --min-seeds) or (citations >= --min-citations)
        and (--min-year is None or year >= --min-year)
```

Defaults are `--min-seeds 2` and `--min-citations 50`. Two independent signals:
something several of your own papers reach is relevant regardless of fame, and
something highly cited is worth seeing even from one seed. `--min-seeds 1
--min-citations 0` records everything.

### A `pending` row suppresses a new row

This is the reverse of how v1 behaved, and the reason is that something now
renders the pending set. `Ledger.decided()` still means `include` or `exclude`
only; `expand` asks `Ledger.seen()` instead — *any* standing row — before
recording a candidate. So a re-run is idempotent and the ledger does not grow by
the whole candidate set every time. The candidate has not been decided; it is
already on the list.

### Deciding a whole set

`--include` and `--exclude` take ids, and also take a selector: `--seed`,
`--via`, `--query`, `--min-year`, `--min-citations`. `--min-year` and
`--min-citations` are the floor when `expand` harvests and a filter over the
pending set when it decides — the same predicate, read twice.

```sh
research-assistant expand --exclude --seed mementos --via citation \
  --reason "forward cites of Mementos are all NVM, not harvesting"
```

A selector-driven write prints the count and the first five titles and asks;
`--yes` skips that for scripts. Preview the exact set first with `reading-list`
and the identical flags — that is the read-only path, and it never writes a row.

## `Reading List.md`, the rendered view

The ledger is the source of truth and the note is a view of it, generated
wholesale every time anything changes the pending set: `expand`, `promote`,
`expand --include`/`--exclude`, and `reading-list` on demand. `--dry-run`,
`--report` and `--adopt` render nothing.

Nothing parses Markdown back into a decision, so the note can be deleted at any
point and rebuilt, and a hand-edit of it is simply overwritten. It is a sibling
of the papers folder, never a note inside it: `papers_dir.glob("*.md")` is
walked by `bib`, `find`, the root scan and `--report`'s note count, and one
forgotten exclusion rule there would silently corrupt a bibliography or a PRISMA
number.

Candidates are grouped by seed and ranked within each group by (seeds reached,
citations, year, title). A candidate two or more of your papers reached leads
the note and is **never** repeated under a single seed, so every row appears
exactly once. A seed whose note has since been deleted renders as `## From W…
(no note in the vault)`; a candidate with no seeds at all lands under
`## Unattributed`.

After hand-editing `screening.tsv`, run `reading-list` to bring the note back
into agreement with it.

## The rule when the two disagree

**A note that exists always wins.** The existence check runs before the ledger
check, so a paper with a note is never re-screened even if the ledger says
`exclude` — adding it *was* the change of mind. `--report` surfaces the
disagreement instead of the code resolving it:

```
  in the vault, not in the ledger          2   added by hand, or before v1
  excluded, but a note exists              1   you changed your mind; the note wins
  included, but no note exists             3   deleted by hand
  rows the parser could not read           0
```

A note **deleted by hand** leaves an `include` row with nothing behind it. The
deletion is the decision and `expand` will not undo it, but the row is now
factually wrong, so `--report` prints the command that would fix it and does not
run it — only you know whether you deleted the note or your sync client did.

## Migration

`expand --adopt` writes one `include` row per note carrying a DOI or an OpenAlex
id, timestamped with the note's own mtime. It refuses if the ledger already has
rows.

The *history* of those decisions is unrecoverable, and `reason: adopted` says so
rather than inventing one.

## What it does not index

`source` records resources no index knows — a datasheet, a slide deck, a
standard — and those carry neither a DOI nor an OpenAlex id, so a row for one
would have no key. The ledger indexes what the citation graph can reach.
