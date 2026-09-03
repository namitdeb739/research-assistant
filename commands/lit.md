Find prior art on a topic and report only what is missing from the research vault.

Usage: `/lit <topic>` — e.g. `/lit multi-tenant sensor network virtualization`

1. **Check what is already tracked**, so nothing already known gets re-reported:
   `research-assistant find "<topic>"`, and `find --topic "<hub>"` for the
   surrounding cluster. Do not read the papers folder directly — a sample of it
   produces a filter that misses things.
2. **Search for prior art.** Prefer the field's own venues, arXiv and author
   homepages over aggregators and blog posts.
3. **Filter hard against step 1.** A paper already in the vault is not a finding.
4. **Report each genuinely new result** as: title · authors · venue · year · DOI,
   plus one sentence on why it matters *for this project specifically* — not a
   summary of its abstract.
5. **Rank by relevance to what the user is working on**, not by citation count. A
   directly-applicable workshop paper beats a famous but tangential one.
6. **Offer to add the useful ones** with `research-assistant paper <doi>`.

Say so explicitly when a search turns up little — a genuinely thin literature is
a finding worth knowing, and padding the list with tangential hits wastes reading
time.
