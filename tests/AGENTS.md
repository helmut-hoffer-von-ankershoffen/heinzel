# AGENTS.md — tests

- Each test file sets exactly one level: `pytestmark = pytest.mark.unit` (or `integration`, `e2e`).
- `unit/` mirrors `src/heinzel/` (`unit/kernel/kernel_test.py` ↔ `src/heinzel/kernel/_kernel.py`); `integration/` goes
  through the CLI head (`CliRunner` or `python -m heinzel`); `e2e/` installs Heinzelmännchen with `uv tool` and runs the
  README. `fixtures/heinzel-dummy` is a minimal plugin with a submodule for the e2e lifecycle tests.
- `make_kernel(builtins=True, example=module, example__sub=submodule, config=..., settings_=...)` builds a kernel from
  test modules (`__` becomes `.`); `settings` gives default settings. Every test gets its own `HZ_CONFIG_HOME`,
  `HZ_DATA_DIR` and `HZ_CACHE_DIR`.
- Fast loop: `uv run pytest tests/unit -x -q` (`--lf` reruns failures).
