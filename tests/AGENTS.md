# AGENTS.md — tests

- Fast loop: `uv run pytest tests/unit -x -q` (`--lf` reruns failures).
- Set exactly one level per file: `pytestmark = pytest.mark.unit` (or `integration`, `e2e`).
- `unit/` mirrors `src/heinzel/` (`unit/kernel/kernel_test.py` ↔ `src/heinzel/kernel/_kernel.py`). `integration/` goes
  through the CLI head (`CliRunner` or `python -m heinzel`). `e2e/` installs with `uv tool` and runs the README.
  `fixtures/heinzel-dummy` is a minimal plugin with a submodule for e2e lifecycle tests.
- `make_kernel(builtins=True, example=module, example__sub=submodule, config=..., settings_=...)` builds a kernel from
  test modules (`__` becomes `.`). Every test gets its own `HZ_*` directories.
- Tests run in CI's terminal (80 columns, no colour; `heinzel.testing.pin_terminal`), so width- or colour-dependent
  output fails locally too.
