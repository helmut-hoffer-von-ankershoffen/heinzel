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

## Setup step (optional)

If people must or may configure the module before using it, declare one setup step: `@module.setup` (or
`required=True`) on a function that asks through an injected `Prompter` (`text`, `choice`, `choices`, `confirm`,
`number`, `secret`) and writes through `ConfigEditor.write("<module>.<key>", value)` and `Credentials.set(...)`. It
becomes `hz <module> setup` and part of `hz setup` ([ADR 0032](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/blob/main/docs/adr/0032-modules-declare-interactive-setup-steps.md)).
Modules without a declared step get none. Test it with scripted answers: `ScriptedPrompter([...])` bound with
`kernel.bind(Prompter, ...)`, or the `hz_prompter` fixture.

## Red flags

- The module imports another module's `_private` file → inject its service instead.
- An operation has more than a few lines of logic → move it into the service.
- The module imports `heinzel.heads` → never; use `render=`.

## Verification

- [ ] `make check` passes
- [ ] `hz system modules` lists the module with its lets
- [ ] `hz <name> <operation> --json` returns valid JSON
