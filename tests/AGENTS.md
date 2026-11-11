# AGENTS.md — tests

- Fast loop: `uv run pytest tests/unit -x -q` (`--lf` reruns failures).
- Set exactly one level per file: `pytestmark = pytest.mark.unit` (or `integration`, `e2e`).
- Put new files under their level's directory. `make test-parallel TEST_LEVELS=integration` also collects
  E2E inventory for the partition guard, then executes only integration cases; runtime marker checks retain deselected
  cases. Four legacy paths in `tests/conftest.py` keep their unit fixtures.
  `--full-level-collection` disables pruning without changing explicit paths or the marker/path check.
  To focus the E2E partition guard, collect `tests/e2e` alongside its integration file and select `-m integration`;
  E2E bodies remain deselected, and a partial inventory fails closed.
- `unit/` mirrors `src/heinzel/` (`unit/kernel/kernel_test.py` ↔ `src/heinzel/kernel/_kernel.py`). `integration/` goes
  through the CLI head (`CliRunner` or `python -m heinzel`). `e2e/` installs with `uv tool` and runs the README.
  `fixtures/heinzel-dummy` is a minimal plugin with a submodule for e2e lifecycle tests.
- `make_kernel(builtins=True, example=module, example__sub=submodule, config=..., settings_=...)` builds a kernel from
  test modules (`__` becomes `.`). Every test gets its own `HZ_*` directories.
- Tests run in CI's terminal (80 columns, no colour; `heinzel.testing.pin_terminal`), so width- or colour-dependent
  output fails locally too.
