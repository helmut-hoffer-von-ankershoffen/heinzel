---
name: add-operation
description: Add an operation (a command in the CLI, a tool in MCP, an endpoint in REST) to an existing Heinzelmännchen module. Use when asked for a new command, subcommand or capability of an existing module.
---

# Add an operation

```python
from typing import Annotated

from pydantic import Field

from heinzel import HeinzelError


@module.operation(read_only=True)  # read_only=False when it changes state
def archive(
    service: ReportService,  # injected by type, hidden from every head; injected parameters come first
    name: Annotated[str, Field(description="Which report to archive.")],  # no default -> CLI argument
    dry_run: Annotated[bool, Field(description="Only show what would happen.")] = False,  # default -> option
) -> ArchiveResult:
    """Archive a report and return where it went."""  # first line = help text, required
    return service.archive(name, dry_run=dry_run)
```

## Rules

- The docstring is mandatory; its first line is the summary in every head.
- Injected parameters (services, settings, `Kernel`) come first, then arguments, then options with defaults.
- `Field` constraints (`ge=1`, `pattern=...`) are validated by the kernel for every head.
- Return pydantic models (or lists of them, or `str`); never print. Add `render=` only for a better human view.
- Name: the function name with `_` → `-`; use `name=` when the natural name clashes with a builtin (`list`).
- An operation named like its module, in a module with no other operations or submodules, collapses into a single
  command (`hz system changelog`).
- Raise `HeinzelError` with an actionable message for expected failures.

## Verification

- [ ] Unit test of the service method, integration test through the CLI (`--json` output asserted)
- [ ] `uv run hz <module> <operation> --help` reads well
- [ ] `make docs` regenerated the CLI and configuration references
- [ ] `make check` passes
