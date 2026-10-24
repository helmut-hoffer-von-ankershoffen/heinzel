---
name: add-module
description: Add a module or submodule to Heinzelmännchen (or to a Heinzelmännchen plugin) the idiomatic way, with service, operations, tests and docs. Use when asked for a new command group, a new area of functionality, or a submodule of an existing module.
---

# Add a module

A module is a package whose `__init__.py` declares `module = Module(name, summary)`. The kernel discovers it; there
is no registry to edit. A package nested in a module's package that declares its own `module` is a submodule.

## Process

1. Decide where it lives: `src/heinzel/modules/<name>/` for core (generic, company-neutral — see INTENT.md tenet 9),
   or a plugin for anything specific. A new top-level concept in the core warrants an ADR (`write-adr`).
2. Create the package:
   ```
   <name>/
     __init__.py    # module = Module(...); module.service(...); @module.operation functions; __all__
     _models.py     # pydantic models the operations return
     _service.py    # the logic, a class whose constructor asks for what it needs by type
     _render.py     # optional: rich renderers used as render= hints
   ```
3. Keep operations thin: annotate parameters `Annotated[T, Field(description=...)]`, take the service by type,
   return a model. Raise `HeinzelError` for failures a user should read.
4. Tests (`write-tests`): unit tests for the service in `tests/unit/modules/`, integration tests through the CLI
   head in `tests/integration/` (build the app with `make_kernel(builtins=True, ...)`).
5. Regenerate the references with `make docs` (`make lint` fails until you do).
6. Check it shows up: `uv run hz system modules`, `uv run hz <name> --help`.
7. Update README (usage) and ARCHITECTURE.md if the module is part of the core story.
8. Optional, for a built-in module or a first-party plugin under packages/ worth showing in the README demo: add a
   `demo.tape` next to its `__init__.py` with a few real `hz` commands (`Type`, `Enter`, `Wait`, `Sleep`, `Hide`,
   `Show`; `Require` for other programs it pipes into), run `make fix` to regenerate docs/assets/demo.tape and
   `make demo` to render the GIF; look at its last frames before committing it. Rules and an example:
   CONTRIBUTING.md, "The README demo".

## Red flags

- The module imports another module's `_private` file → inject its service instead.
- An operation has more than a few lines of logic → move it into the service.
- The module imports `heinzel.heads` → never; use `render=`.

## Verification

- [ ] `make check` passes
- [ ] `hz system modules` lists the module with its lets
- [ ] `hz <name> <operation> --json` returns valid JSON
