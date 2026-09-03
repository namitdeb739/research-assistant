# The note format

This is the public API. The Python is an implementation detail — you can throw
it away and keep your vault. What you cannot throw away is the shape of the
notes, because Obsidian, your queries, your `.base` table views and every other
tool you point at the folder all depend on it.

**The version number is a promise about this document, not about function
signatures.** A minor release may add a frontmatter key or a body section; only
a major release may remove or repurpose one, or change what a tool is allowed
to overwrite.

## Layout

```text
<papers-dir>/          one note per resource, named <cite_key>.md
  ../pdfs/             open-access PDFs, named <cite_key>.pdf
  ../topics/           generated topic hub notes, one per theme
```

`pdfs/` and `topics/` are siblings of the papers folder, not children of it.

## Frontmatter

Written in this order, every key always present — `null` rather than absent, so
a table view has a column and a missing value is visibly missing.

| Key | Type | Meaning |
|---|---|---|
| `title` | string | As the publisher records it. |
| `cite_key` | string | `surnameYEARword`. A **recorded fact**, not recomputed on each run, so the key in your bibliography is stable. |
| `entry_type` | string | BibTeX entry type: `article`, `inproceedings`, … |
| `authors` | list | Full names, in publication order. |
| `year` | int \| null | |
| `venue` | string \| null | |
| `doi` | string \| null | |
| `openalex_id` | string \| null | The citation graph's join key, and the only identifier for works that have no DOI (USENIX mints none). |
| `url` | string \| null | Landing page, when there is no `doi.org` link to give. |
| `pdf` | wikilink \| null | `[[<cite_key>.pdf]]` once a copy is filed. |
| `pdf_url` | string \| null | Where the open-access copy can be fetched. |
| `code_url` | string \| null | |
| `citations` | int \| null | |
| `open_access` | string \| null | OpenAlex's status, lowercased. |
| `topics` | list of wikilinks | `[[hub note]]`, rewritten by `relink`. |
| `cites` | list of wikilinks | Rewritten by `relink` to notes that actually exist. |
| `tags` | list | See below. |

### The rule frontmatter obeys

Frontmatter holds only **intrinsic** properties — facts about the resource. A
judgement about it (any good? related work?) or progress through it (read yet?)
belongs in prose, never in a property: a five-point scale in a table is a worse
record of an opinion than a sentence is.

`topics` is the exception, because it describes the paper rather than your view
of it.

### Tags

`paper` on every paper note, `topic` on every hub. `harvested` marks a note the
graph walk added, `seed` a note it expanded from — together they are what keeps
`expand` from quietly reaching depth 2 on a re-run.

`read` is the second exception to the intrinsic rule, and only because nobody
sets it by hand: it is **derived** from whether the body holds a takeaway or
quotes of your own, so it states a fact about the note rather than a claim you
have to keep true. Empty those sections and it comes off again. An abstract does
not count — those are backfilled from OpenAlex without anyone reading a word.

### Reverse links

There is no `cited_by`. Obsidian derives that direction in "Linked mentions",
and a second copy of a relation is a second thing to keep in sync.

## Body

Four `##` sections, in this order, any of which may be empty:

```markdown
## Key takeaway

## Abstract

## Notes

## PDF
```

`## PDF` holds the embed and goes last: a rendered PDF is tall and the notes
matter more. Headings not in this list are **preserved** — the renderer moves
section content, it never rewrites it.

## The one region a tool owns

Inside `## Notes`:

- Everything **above the first `### ` heading is yours**, and is reattached
  byte-for-byte on every write.
- Everything **from that heading down is machine-owned**, and is rewritten
  wholesale by `highlights --apply`.

So do not use `### ` headings in your own prose — bold or plain paragraphs
instead. Bullets of your own are fine; only a heading opens the machine region.

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
notice, and a `## Papers` list. Anything hand-written in one is lost on the next
run — write it in the paper note instead.

A topic earns a hub only once several papers share it. A label on one paper of
two hundred is graph hair, not a cluster.
