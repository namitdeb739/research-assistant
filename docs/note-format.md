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
```

`pdfs/` and `topics/` are **siblings** of the papers folder, not children of it.

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
| `doi` | string \| null | `paper` | |
| `openalex_id` | string \| null | `paper` · `relink` | The citation graph's join key, and the only identifier for works with no DOI. |
| `url` | string \| null | `paper` · `source` | Landing page, when there is no `doi.org` link to give. |
| `pdf` | wikilink \| null | `paper` · `tidy` | `[[<cite_key>.pdf]]` once a copy is filed. `tidy` adopts a hand-saved match. |
| `pdf_url` | string \| null | `paper` | Where the open-access copy can be fetched. |
| `code_url` | string \| null | `paper` | |
| `citations` | int \| null | `paper` | |
| `open_access` | string \| null | `paper` | OpenAlex's status, lowercased. |
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
