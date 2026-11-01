# Playbooks: your first contract from a plugin

A contract keeps one asset true ([WORKFLOWS.md](HEINZELMAENNCHEN.md)). A **playbook** is what a plugin brings so you don't
start from a blank one: the asset types a kind of team work keeps (say, a rolling weekly report document and the
team's tracker), hints for finding yours in what you can already read, template contracts that keep them true, and
recommended settings. The core knows no playbook of its own; it lists, discovers, offers and measures
([ADR 0037](adr/0037-plugins-bring-playbooks.md)).

The running example is a weekly report playbook: it drafts this week's report every Friday at 17:00 from the week's
merged pull requests, changed tracker items, the previous report and the team's tracking document, in the previous
report's structure and tone. The draft is a local file; you edit it and send it yourself.

## 1. Install a plugin with a playbook, and look at it

```bash
hz system plugin search --text playbook     # find one; `hz system plugin add <name>` installs it
hz heinzel playbooks                       # its asset types, templates and recommended settings
```

The core brings one, the graph keeper (`graph:team-graph-keeper`, [Teams](TEAMS.md#is-the-work-flowing)); the reference company
[heinzel-acme](https://github.com/helmut-hoffer-von-ankershoffen/heinzel-acme) ships a weekly-report playbook. For your own kind of work, [write one](#writing-a-playbook) in your own
plugin (`hz system plugin new`). Commands below name a playbook's templates as `<module>:<template>`, where `<module>` is
the module `hz heinzel playbooks` shows.

The recommended settings are only proposals, each shown with the `hz system config set` command that applies it.
A playbook may recommend only capability bindings (`code-host`, `tracker`, `documents`, …, never the agent runner)
and its own module's settings; the vendor's CLI or API is the adapter's transport, set up as the adapter's README
says.

## 2. Set it up

```bash
hz setup <module>     # or `hz setup`, which runs every module's step
```

The step offers each recommended setting and applies it only if you say yes to it. It runs the playbook's hints through
the capabilities you have bound (read only) and asks which candidate is your asset, or lets you fill it in later.
Then it offers each template contract and writes it into your workflows directory under a name you choose; an
existing contract is never overwritten. Settings you just applied count from the next command: run the step again
to discover through them.

Without setup, or for an asset no hint finds:

```bash
hz heinzel discover                                                     # candidates, by asset type
hz heinzel new weekly-report --template <module>:weekly-report \
  --asset weekly-report=<document ref> --asset team-project=APP         # fill the placeholders you know
```

A placeholder you leave out stays as `{{asset:<type>}}` in the file; replace it there. Until you do, the contract
isn't ready, and `hz heinzel check` lists each one left (`HZC025`). Templates never come with an active schedule.

## 3. Try it, draft it, schedule it

```bash
hz heinzel show weekly-report             # requirements, adapters, and what to run next
hz heinzel wake weekly-report --dry-run    # fetches the inputs and prints the packet; no agent runs, nothing is spent
hz heinzel wake weekly-report              # drafts it now, within the contract's cost cap
hz heinzel drafts --name weekly-report --latest
```

Next to every draft a `.record.json` holds its lineage: which inputs were read, with which arguments, the SHA-256 of
each input's data, the agent's report and how long drafting took. When the drafts read right, uncomment the
template's `schedule: "0 17 * * FRI"`: five cron fields in your machine's local time (`TZ`, or the system's zone),
daylight saving included. `hz system daemon install` then drafts it at the first tick after Friday 17:00; to have it
waiting at 17:00 sharp, schedule it a little earlier.

## 4. Send it, then measure

Edit the draft and send it the way you always do. Then tell hz what you sent, from where it lives:

```bash
hz heinzel sent weekly-report --file ~/reports/week-44.md --edit-minutes 10   # a file you saved
hz heinzel sent weekly-report --ref <document ref>                           # the document, through `documents`
```

hz compares it with the newest draft (`--draft <file>` picks another) and records, next to the draft, the share of
words you changed, both word counts and the approved text's SHA-256; the text itself is not copied. `--edit-minutes`
is your own estimate and optional. After a few Fridays, `hz heinzel show weekly-report` has a **Success test** table:
each draft's drafting time, cost, share changed and editing estimate, with the median drafting time and the mean
share changed below it. It stays on your machine and is about the drafts, never about people.

## Writing a playbook

A playbook is a few lines in a plugin's module ([ARCHITECTURE: Plugins](ARCHITECTURE.md#plugins)):

```python
from pathlib import Path

from heinzel import AssetType, ChatDraftTemplate, DiscoveryHint, Module

module = Module("teamwork", "Team rituals: the weekly report.")
module.playbook(
    "Draft the team's weekly report every Friday from the week's work and the previous report.",
    assets=[
        AssetType(
            name="weekly-report",
            description="The rolling document the team's reports live in.",
            hints=[DiscoveryHint(read="documents.read", args={"ref": "doc-123"}, match="(?i)weekly report")],
            kept_by="weekly-report",
        ),
        AssetType(name="team-project", description="The tracker project the team plans in."),
    ],
    templates=[Path(__file__).parent / "templates"],  # weekly-report.prose.md, with {{asset:...}} placeholders
    chat_drafts=[ChatDraftTemplate(name="report-starter", text="Ask what the person wants in this report.")],
    defaults={"capabilities.tracker": "github"},  # proposals; applied only on yes
)
```

- A hint is a contract input's shape (`read`, `args`, `window`) plus `match`, a pattern on each item's title. Today's
  capability reads work within a known repository, project or document, so hints confirm and narrow; searching all
  documents by title or folder needs listing reads that `documents` doesn't have yet.
- Placeholders go in double-quoted strings in the frontmatter, e.g. `args: {ref: "{{asset:weekly-report}}"}`;
  anywhere else, and a template with an active `schedule:`, is refused when the plugin is imported. Write it to a
  directory you check with `hz heinzel new x --template <module>:<template>` and `hz heinzel check`.
- `chat_drafts` supplies bounded, untrusted starter text for the chat workshop. A person reviews the resulting
  proposal separately; it grants no access and starts no schedule. Chat drafts are not legacy workflow templates,
  so `hz heinzel new --template` and the legacy setup walk do not create them. Names must not overlap `templates`.
- `defaults` may name `capabilities.<capability>` (never `agent_runner`) and your module's own `<module>.<key>`
  settings; anything else is refused.
- `kept_by` names the template whose output keeps the asset true. Drafts are local today; writing the draft where the
  asset lives, approved in place, is planned.
- `module.playbook` declares the module's setup step; a module with its own step passes `setup=False` and asks for
  `heinzel.kernel.PlaybookSetup` in it.

## Layers: company, org, site, team

A company's playbooks come in layers ([ADR 0040](adr/0040-layers-and-playbooks-are-plugins.md)): one plugin per
company, org, site and team, each declaring where it sits. The made-up Acme:

```python
module = Module("acme-payments-checkout", "The Checkout team of Acme Payments.")
module.layer(parent="acme-payments", site="acme-lisbon", roles=["checkout-captain"])
module.playbook("How Checkout tracks its work.", defaults={"capabilities.tracker": "acme-payments-checkout"})
```

- `parent` and `site` name other layers by module name; the company has no parent. Layers have no kind: company,
  org, site and team are labels from position (the root; a layer with layers below; a layer named as a site; a leaf).
- Name a layer by its path, joined with dashes: `acme`, `acme-payments`, `acme-payments-checkout`, and the site
  `acme-lisbon`. Doctor hints at a name that doesn't follow it; nothing refuses it.
- List the parent's and the site's plugins as dependencies, so installing the team installs its stack. A missing
  parent or site, a cycle, a layer two plugins declare, a layer on a submodule and a site in the layer's own line are
  doctor findings with their fix.
- Your layer is the plugin your team repository's `hz.yaml` names, or `[team] layer` when you set it, or the only team
  layer installed. `hz system layer show` prints your stack, `acme → acme-payments + acme-lisbon →
  acme-payments-checkout`, the roles it adds and the settings its playbooks recommend, most specific first, each with
  the more general layers' values as alternatives. `hz org sync` lists the bindings among them beside what you have
  and writes the ones you accept; `hz setup` walks the layers' playbooks in that order, and a setting a more specific
  layer recommends too is offered only with that layer's.
- Where an org and the site recommend different values and the team is silent, nothing is offered: doctor names both
  layers and the key, and your file decides until the team's playbook sets it. That rule is
  `ORG_SITE_PRECEDENCE` (`team_decides`) in the kernel, the one place to change while the question is open; a
  plugin can't change it.
