# Docs style check

`scripts/check_docs.py` checks what a machine can about hand-written Markdown (the root documents and `docs/`, without
generated references, plugin pages, the catalog, research and the changelog), the strings of `web/src/i18n.ts` and the
`description=` of settings fields. It runs in `CI (the lint job)` and therefore in `make check` and CI.

| Rule | Fails on |
| --- | --- |
| filler | simply, just (adverb), seamless, powerful, robust, leverage, in order to, "this section describes", "as mentioned above", "it is important to note" |
| claims | autonomous, automatically, "while you sleep", sends, publishes, delivers, in the README, guides, vision, catalog, web strings and settings |
| residue | retrofit, migration, migrated, PR numbers, scratchpad, "decided by the maintainer" |
| date | ISO dates in prose |
| version | `0.x.y` in the README and guides |
| install | a commit hash or `#subdirectory=` in a `plugin add` block |
| command | an `hz` command that is not in the command tree |
| paragraph | more than five sentences (three is a warning) |
| table | more than four columns (a warning) |

Warnings fail only with `--strict`. After `make docs-site`, `check_docs.py --site site/` fails on raw `|` table syntax in
the rendered text.

Legitimate exceptions go into `docs/.docs-check-allow.toml`, each with a reason. Known violations that are not fixed yet
go into `docs/.docs-check-baseline.txt` as `file:rule` lines; the check tolerates them and reports an entry that no
longer fires as stale, so delete it. Never add a new entry to the baseline: fix the text.
