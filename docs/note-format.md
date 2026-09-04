# The note format

This is the public API. The Python is an implementation detail: you can throw it
away and keep your vault. What you cannot throw away is the shape of the
notes, because Obsidian, your queries, your `.base` table views and every other
tool you point at the folder all depend on it.

> **The version number is a promise about this document, not about function
> signatures.** A minor release may add a frontmatter key or a body section;
> only a major release may remove or repurpose one, or change what a tool is
> allowed to overwrite.

## Layout

```text
<papers-dir>/
├── ALFRED - Virtual Memory….md   one note per resource, named for its title
└── …
../pdfs/
├── maioli2021alfred.pdf     open-access PDFs, named <cite_key>.pdf
└── …
../topics/
├── Energy Harvesting.md     generated hub notes, one per shared theme
└── …
../screening.tsv             what was looked at and turned down
../Reading List.md           the pending rows of the ledger, rendered
```

`pdfs/`, `topics/`, `screening.tsv` and `Reading List.md` are **siblings** of
the papers folder, not children of it. The ledger is not part of this format and
has its own: [`docs/screening.md`](screening.md). `Reading List.md` is a
generated view of that ledger and is **not a paper note**: it has none of the
frontmatter below, and nothing reads it back.

A note is named for its **title**, sanitised for the filesystem; a PDF is named
for its **cite key**. So the filename guards nothing about the key: two papers
can carry one `cite_key` and still get two filenames. `bib --check` is what
catches that.

## Frontmatter

Written in this order, every key always present, `null` rather than absent, so a
table view has a column and a missing value is visibly missing.

| Key | Type | Maintained by | Meaning |
|---|---|---|---|
| `title` | string | `paper` · `source` | As the publisher records it. `tidy` repairs HTML entities. |
| `cite_key` | string | `paper` · `source` | `surnameYEARword`. A **recorded fact**, never recomputed, so the key in your bibliography is stable. |
| `entry_type` | string | `paper` · `source` | BibTeX entry type: `article`, `inproceedings`, … |
| `authors` | list | `paper` · `source` | Full names, in publication order. |
| `year` | int \| null | `paper` · `source` | |
| `venue` | string \| null | `paper` · `source` | |
| `volume` | string \| null | `paper` · `tidy --bibfields` | |
| `number` | string \| null | `paper` · `tidy --bibfields` | Crossref calls it `issue`; BibTeX calls it `number`. |
| `pages` | string \| null | `paper` · `tidy --bibfields` | As the publisher gives it, so `1-28` and `1--28` both occur. |
| `publisher` | string \| null | `paper` · `tidy --bibfields` | |
| `editors` | list | `paper` · `tidy --bibfields` | Full names. Rendered only for the entry types that take one. |
| `month` | string \| null | `paper` · `tidy --bibfields` | A bare number; the BibTeX style decides how to render it. |
| `doi` | string \| null | `paper` | |
| `openalex_id` | string \| null | `paper` · `relink` | The citation graph's join key, and the only identifier for works with no DOI. |
| `url` | string \| null | `paper` · `source` | Landing page, when there is no `doi.org` link to give. |
| `pdf` | wikilink \| null | `paper` · `tidy` | `[[<cite_key>.pdf]]` once a copy is filed. `tidy` adopts a hand-saved match. |
| `pdf_url` | string \| null | `paper` · `relink` | Where the open-access copy can be fetched. |
| `code_url` | string \| null | `paper` | |
| `citations` | int \| null | `paper` · `relink` | |
| `open_access` | string \| null | `paper` · `relink` | OpenAlex's status, lowercased. |
| `retracted` | string \| null | `paper` · `health --fix` | Crossref's strongest `updated-by` notice: `retraction`, `withdrawal`, `removal`, `partial_retraction` or `expression_of_concern`. **Derived**, never set by hand, and cleared again if the notice is withdrawn. |
| `topics` | list of wikilinks | **`relink`** | `[[hub note]]`. Rewritten wholesale. |
| `cites` | list of wikilinks | **`relink`** | Rewritten wholesale, to notes that actually exist. |
| `tags` | list | `expand` · `tidy` | See [Tags](#tags). |

### The rule frontmatter obeys

Frontmatter holds only **intrinsic** properties, facts about the resource. A
judgement about it (any good? related work?) or progress through it (read yet?)
belongs in prose, never in a property: a five-point scale in a table is a worse
record of an opinion than a sentence is.

Two deliberate exceptions:

| Exception | Why it is allowed |
|---|---|
| `topics` | Describes the paper, not your view of it. |
| the `read` tag | **Derived**, never set by hand. See below. |

`retracted` is not a third exception. A retraction is a fact the registrar
asserts about the resource, so it is intrinsic in the ordinary way, and like the
`read` tag it is derived rather than claimed: nothing sets it by hand and a
withdrawn notice clears it again.

### Key order

The order in the table above is the order every note is written in, and it is
recorded once, in `vault.FRONTMATTER_KEYS`. A key added by a later release lands
after `tags` on notes that predate it, because `update_frontmatter` appends what
it has not seen. `tidy` puts them back, filling absent keys with `null` (or `[]`
for the list-valued ones) and keeping any property added by hand after the known
ones.

### Tags

| Tag | On | Set by |
|---|---|---|
| `paper` | every paper note | `paper`, `source`, `expand` |
| `topic` | every hub note | `relink` |
| `seed` | a note the graph walk expanded *from* | `expand` |
| `harvested` | a note the graph walk *added* | `expand` |
| `read` | a note whose `## Notes` holds anything | `tidy` (derived) |

`seed` and `harvested` together are what keeps `expand` from quietly reaching
depth 2 on a re-run.

`read` is derived from whether `## Notes` holds anything, whether prose of your
own or quotes recovered from the PDF, so it states a fact about the note rather
than a claim you have to keep true. Empty that section and it comes off again. An
abstract does not count: those are backfilled from OpenAlex without anyone
reading a word.

### Reverse links

There is no `cited_by`. Obsidian derives that direction in "Linked mentions",
and a second copy of a relation is a second thing to keep in sync.

## Body

Three `##` sections, in this order, any of which may be empty:

```markdown
## Abstract

## Notes

## PDF
```

`## PDF` holds the embed and goes last: a rendered PDF is tall and the notes
matter more. Headings not in this list are **preserved**: the renderer moves
section content, it never rewrites it.

`## Key takeaway` was a fourth section until 0.2.0. It is now an ordinary
hand-written heading. `tidy` drops it where it is empty, which is the migration,
and preserves it wherever somebody actually wrote under it.

0.3.0 added seven keys: `volume`, `number`, `pages`, `publisher`, `editors` and
`month`, without which a generated `@article` rendered with no volume or page
range; and `retracted`. All are additive, so a note written by 0.2.0 stays
readable. `tidy` is the migration: it puts the frontmatter back in order, and
`tidy --bibfields` fills the six bibliographic ones from Crossref.

## What a tool may overwrite

`## Notes` has no delimiters. The contract is **positional**: the first `### `
heading is the boundary.

```markdown
## Notes

Worth reading against Alfred: the checkpoint cost here is
measured, not modelled.                            <-- YOURS: reattached
                                                       byte-for-byte on
- my own bullet, fine here                             every write

======== the first `### ` heading: the boundary ============================

### SMFC power output                              <-- MACHINE-OWNED:
                                                       rewritten wholesale
- produce as much as 200 µW ([[…#page=2|p. 2]])        by `highlights
- open-circuit voltage upwards of 731 mV               --apply`
```

So **do not use `### ` headings in your own prose**; use bold or plain
paragraphs. Bullets of your own are fine, and only a heading opens the machine
region.

The heading text itself is the one thing in a note a model writes. Which quotes
share a heading, and what that heading says, are its judgement; the quotes under
it are not.

Across the whole note:

| Region | Owner | On write |
|---|---|---|
| Frontmatter keys above | the commands in that column | Overwritten in place |
| `## Abstract` | `paper`, `tidy --abstracts` | Filled only when empty |
| `## Notes`, before the first `### ` | **you** | Reattached byte-for-byte |
| `## Notes`, from the first `### ` | `highlights --apply` | Rewritten wholesale |
| `## PDF` | `paper`, `tidy` | Embed regenerated |
| any other `##` heading | **you** | Preserved, moved but never rewritten |
| `topics/*.md` | `relink` | **Regenerated wholesale** |

## Quotes are never altered

Text recovered from a PDF's highlights is verbatim. The only transformations are
ligature expansion, Unicode NFC, line-break de-hyphenation and whitespace
collapsing, and they are applied identically by the writer and by `--audit`. A
typo in the source survives into your notes. That is the point: a quote you
cannot trust is not evidence.

`--apply` addresses quotes by their `order` number, never by their text, so the
write path is structurally incapable of rewriting one.

## Topic hubs

Regenerated wholesale by `relink`, so a topic losing a paper leaves no stale
link. They carry `title`, `papers` (a count) and `tags: [topic]`, a generated
notice, and a `## Papers` list.

> Anything hand-written in a hub note is **lost on the next run**. Write it in
> the paper note instead.

A topic earns a hub only once several papers share it. A label on one paper of
two hundred is graph hair, not a cluster.
