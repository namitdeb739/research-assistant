# The screening ledger

`expand` acquires. Until this existed, nothing triaged: a candidate was filtered
only against the notes currently present, so a paper you deleted came back on
the next run, and there was no record that you had looked at it and said no.

A vault that grows by harvest and shrinks by deletion is a set of arrivals. The
ledger is what makes it a set of decisions, and it is the only thing that can
answer *how did you build this bibliography* in a methods chapter.

## Where it lives, and why it is not frontmatter

```text
<papers-dir>/          the notes
../pdfs/
../topics/
../screening.tsv       ← here, a sibling
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
# research-assistant screening v1
decided	decision	openalex_id	doi	year	via	seeds	reason	title
2026-09-04T11:20:03+00:00	exclude	W2755950973	10.1145/3132211	2017	reference	W2300484078	survey, not primary	A Survey of Energy Harvesting
```

Nine tab-separated columns, **no quoting**. The column order is the escaping
strategy: fixed-alphabet fields first, the two free-text fields last, `title`
last of all. Tabs and newlines are flattened to single spaces on write, and on
read any extra tabs are rejoined into `title` — so a hand-edit can only ever
damage the human-readable column, never shift a decision into the wrong field.

Quoting would let a title carry a newline, and then the file stops being
greppable and `wc -l`-able, which is most of the point of a TSV. The ledger is
not the metadata store; the note is. `title` is a courtesy label.

| Column | Meaning |
|---|---|
| `decided` | Full UTC timestamp, so ordering is total and file order only breaks ties |
| `decision` | `include`, `exclude` or `pending` |
| `openalex_id` · `doi` | Either or both. A lookup tries the id first, then the DOI |
| `via` | How it was found: `reference`, `citation`, `related` |
| `seeds` | Which root notes led to it — provenance about the *decision* |
| `reason` | Required for `exclude` |

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
research-assistant expand                      # as before, plus an include row each
research-assistant expand --screen             # record candidates as pending only
research-assistant expand --exclude W123 --reason "survey, not primary"
research-assistant expand --include W123       # how you un-exclude
```

`--dry-run` still writes **nothing**, ledger rows included: a row is a write.

`--limit` **defers rather than decides**. A paper cut by the cap was not judged,
so it gets no row and is offered again next time.

A `pending` row does not suppress anything. It is a note to yourself that the
paper is waiting on a decision, and `expand` will keep offering it until one is
recorded.

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
