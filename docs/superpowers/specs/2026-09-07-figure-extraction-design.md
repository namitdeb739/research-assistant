# Figures and tables into a paper note

A figure is evidence like a quote, and the vault has no way to carry one. This
adds that path without adding a second machine region, a second grouping
mechanism, or a model that decides what is relevant.

## The problem with "relevant"

Picking relevant figures is not a ranking problem. The vault's whole contract is
that a model decides which quotes belong together and what to call the group,
and never a word of the quotes themselves. A relevance model choosing figures
would break that: it would be the first place a tool decided what the paper
says.

So it does not choose. **You choose, by drawing a box.** The tool crops what you
bounded, and its only judgements are geometric.

## The signal

Three candidate signals were probed against `garca2020iotbased.pdf`, a real
annotated paper in the vault (1262 annotations: 1250 `/Link`, 11 `/Highlight`,
1 `/Square`).

| Signal | Verdict |
|---|---|
| Free-draw (`/Highlight` with no `/QuadPoints`) | **Dead.** No geometry is recorded at all. `/Rect` is `[0 0 FLT_MAX FLT_MAX]`, as `tests/fixtures/make_highlights_pdf.py:35` already documents. |
| A selection catching no text (`NO_TEXT`) | **Unreliable.** All 11 highlights in the probed file carry real quads; the reason never fires. A drag over a figure with no text layer beneath it produces no selection to highlight. |
| A drawn rectangle (`/Square`) | **Works.** Exact `/Rect`, cropped pixel-perfectly, and currently ignored: `extract()` filters on `Subtype == "Highlight"` and drops every other subtype without even recording it as skipped. |

A fourth was rejected before implementation. **Caption-anchored region inference**
— detect `Figure N.`, walk to the enclosing whitespace block — fails on this
corpus: every caption is drawn two or three times at slightly different
geometry (`Figure 3.` appears at y=435, 433 and 432), and several report
coordinates off the page entirely (`y=-100`, `y=1.0`, and Figure 5's caption
surfacing at `y=841` on page 7 when it belongs on page 6), because those
glyphs sit inside transformed XObjects. Cropping a rect does not read the text
layer and is immune to all of it.

The `/Square` probed was written by Apple Preview, not Obsidian (`AAPL:AKExtras`
is in its key set). The design is therefore tool-agnostic: any drawn shape
counts.

## What counts as a figure

An annotation is a figure when all four hold:

1. `/Subtype` is one of `Square`, `Circle`, `Polygon`, `Ink`.
2. Its `/Rect`, normalised so `x0 < x1` and `y0 < y1`, has a non-empty
   intersection with the page's mediabox.
3. The clipped rect covers **less than 90%** of the page area.
4. The clipped rect covers **at least 100 pt²** (a 10×10pt box).

Rule 3 is the free-draw guard and it must be an area test, not a finiteness
test: `FLT_MAX` is `3.4e38`, a finite float, and clipping it to the mediabox
yields exactly the whole page. An annotation failing rule 3 is recorded as
`skipped` with the existing `FREE_DRAW` reason, so the stderr line you already
get keeps meaning what it meant. Rule 4 drops a stray click.

A highlight is admitted by its **quads**; a figure by its **rect**. Using each
shape's native geometry is why a circle and a scribble reduce to the same box
with no special case for either.

## One sequence

Figures take their `order` from `_order_key`, the existing `(page, -top, left)`
sort, which needs no change: a rect has a top and a left exactly as a quad does.
Box Figure 6 and it takes its number between the quote above it and the quote
below it.

This is the load-bearing decision. Because there is one sequence and not two,
`group_quotes`, `split_notes`, `grouping_of`, `score_groupings` and `--audit`
learn nothing new. A figure is placed under a `### ` heading by the same
grouping of integers, so the model still writes only headings, and still cannot
touch a quote.

## Module boundary

`figures.py` is new and does exactly one thing: **a PDF, a page and a rect give
you a deterministic PNG on disk.** It knows nothing about notes, quotes,
headings, cite keys or markdown, and it is the only module that imports
`pypdfium2`. If that library ever has to be swapped, one file changes.

`highlights.py` gains the figure arm of the annotation loop and emits figure
items carrying a `rect` and no asset. It does **not** import `figures`. The CLI
joins them: it renders each figure's rect and fills in the asset path. So the
raster dependency stays out of the module that owns the note format.

## The asset

**Where.** `assets/<cite_key>/`, resolved as `papers_dir.parent /
vault.ASSETS_DIRNAME`, exactly as `pdfs/` already is at `cli.py:228`. No
`--assets-dir` flag and no new environment variable, because `pdfs/` has
neither and a second convention for one idea is worse than the convention.

**Name.** `p07-3f9a1c2b.png`. The hex is a truncated BLAKE2b over the full input
to the render: page number, rect rounded to 0.1pt, render scale, and the
`draw_annots` setting. Rounding stabilises float formatting, and 0.1pt is
0.035mm, so nothing you could draw distinguishes two boxes it merges.

Making the filename a hash of the render inputs is what buys the properties:
the same box always resolves to the same file, so a re-run rewrites nothing;
nudging an annotation orphans one file and creates one; and bumping the scale
constant renames every file rather than leaving stale content behind an
unchanged name.

**Determinism, stated exactly.** The PNG bytes are *not* claimed reproducible
across `pypdfium2` versions. The guarantee is weaker in form and stronger in
effect: **if the file exists it is not re-rendered.** Version drift therefore
cannot churn the vault, because a second run rasterises nothing it has already
rasterised.

That guarantee carries a precondition the first draft of this document missed.
If existence alone is trusted, an asset must never exist half-written, so it is
staged beside its target and moved into place with `os.replace` — atomic within
one filesystem, which is why the staging file is a sibling rather than a temp
directory. Written in place instead, a crash mid-write would leave a truncated
PNG that satisfies the existence check forever and no re-run would repair: the
one way this design could silently poison a vault.

**Rendering.** `pypdfium2`, one new dependency. `draw_annots=False`, so the crop
is the paper rather than the paper with your own box drawn across it. Scale is a
documented constant at 200 DPI: 150 is legible but soft on caption serifs, 300
roughly doubles the bytes for a difference invisible in Obsidian's preview.

**Encoding.** The PNG is written with `zlib` and `struct` from the standard
library, about 25 lines for a fixed truecolour 8-bit image. This follows the
precedent of `tests/fixtures/make_highlights_pdf.py`, which hand-writes an
entire PDF for stated reasons about reviewable inputs, and it keeps the byte
layout ours rather than a second binary wheel's. Pillow is the boring
alternative and would replace exactly one function.

## What a figure looks like in a note

```markdown
### Monitored parameters

- ![[assets/garca2020iotbased/p07-3f9a1c2b.png]] ([[garca2020iotbased.pdf#page=7|p. 7]])
- most used is p tahraem oenteer sbased on ultrasound ([[garca2020iotbased.pdf#page=7|p. 7]])
```

One figure is one bullet, closed by the same page link a quote carries, under
the same kind of heading. The embed uses the **vault-relative path** rather than
a bare filename, because assets are nested one folder per paper and a bare
`p07-3f9a1c2b.png` relies on Obsidian's shortest-unique-path resolution.

`split_notes` parses this back with no change: `_TRAILING_LINK` strips the page
link and leaves the embed as the item's text. To keep `Highlight.text` meaning
*what the page says* — which is the entire claim `--audit` makes — a figure's
`text` stays empty and the embed lives in a separate `asset` field on both
`Highlight` and `Quote`. `_item` renders the embed when `asset` is set, and
identity keys become `(page, text, asset)`.

Audit consequently works on figures for free: the asset name is a pure function
of the geometry, so moving an annotation shows up as drift, which is correct.

## Tables

A table is cropped exactly as a figure is: one code path, one asset kind. The
cost is accepted knowingly — the numbers are a picture, so they are neither
searchable nor copyable from the vault. Markdown reconstruction was rejected
because spanning cells, multi-line cells and rule-less tables make it
undeterministic, and a silently mis-parsed table is a note that misrepresents
its source, which is the one failure this project will not accept.

## Note format

Additive, so **0.5.0**. `assets/` joins the layout diagram, `## Notes` is
documented as possibly holding embeds among its bullets, and the "what a tool
may overwrite" table gains the assets folder. Nothing existing is removed or
repurposed. A note written by 0.4.0 stays valid, and a vault with no drawn
boxes is byte-identical after the upgrade.

## Deliberately out of scope

- **Pruning orphaned assets.** A moved annotation leaves one unreferenced PNG.
  It is harmless, rare, and a `--prune` is a separate decision about deleting
  files in someone's vault.
- **Caption text as a figure's label.** The probe shows caption geometry is not
  trustworthy on this corpus. The heading names the group already.
- **Figures from PDFs you have not annotated.** No annotation, no figure. That
  is the contract, not a limitation to fix later.
