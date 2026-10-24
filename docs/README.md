# Documentation

Find what you need by who you are. Files marked *generated* are written by `make docs` from the code, and CI fails when
they are out of date, so they can't drift.

| You want to… | Read |
| --- | --- |
| **Get started**: bootstrap a team as its manager, or join one with the line your manager posted | [guides/manager.md](guides/manager.md) · [guides/joining.md](guides/joining.md) |
| **Use** Heinzelmännchen: install, commands, configuration | [README](../README.md) · [MODULES](MODULES.md) *(generated)* · [CLI_REFERENCE](CLI_REFERENCE.md) *(generated)* · [CONFIGURATION](CONFIGURATION.md) *(generated)* |
| **Write workflows**: the journey from nothing to a scheduled team contract, and the contract reference | [WORKFLOWS](WORKFLOWS.md) · [playbooks: your first contract from a plugin](PLAYBOOKS.md) |
| **Work as a team**: create, join and add to the team repository | [TEAMS](TEAMS.md) |
| **Understand** why it exists and how it is built | [INTENT](../INTENT.md) · [ARCHITECTURE](ARCHITECTURE.md) · [decisions (ADRs)](adr/README.md) · research: [daemon head](research/daemon-head.md) · [workflow contracts](research/workflow-contracts.md) · [embedded workflows](research/embedded-workflows.md) · [inventory](research/inventory.md) · [people and org](research/people-and-org.md) |
| **Extend** it with modules and plugins | [README: Extend](../README.md#extend) · [ARCHITECTURE: Plugins](ARCHITECTURE.md#plugins) · [writing a playbook](PLAYBOOKS.md#writing-a-playbook) · [settings that need no explaining](SETTINGS_UX.md) · the template's AGENTS.md (`hz system plugin new`) |
| **Contribute** to the core | [CONTRIBUTING](../CONTRIBUTING.md) · [CODING_STANDARDS](CODING_STANDARDS.md) · [CHANGELOG](../CHANGELOG.md) *(generated at release)* |
| Work on it **as a coding agent** | [AGENTS](../AGENTS.md) and the scoped AGENTS.md files it lists |
| Report a **vulnerability** | [SECURITY](../SECURITY.md) |

How drift is prevented:

- Everything that can be derived from the code is generated: the module catalog, the CLI and configuration
  references, the README's module list, the changelog and the release notes.
- What can't be generated is checked: every operation needs a docstring and every parameter and setting a
  description (they become help text in every head), every relative link must resolve, and every document in `docs/`
  must be listed here.
- The README's install, use and uninstall instructions are run as tests on macOS and Linux.
- The `docs-reviewer` agent reviews prose against the code in every pull request that changes behaviour.
