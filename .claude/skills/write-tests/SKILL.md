---
name: write-tests
description: Write Heinzelmännchen tests at the right level - unit, integration or e2e, as defined in the coding standards (a public SDK's taxonomy). Use whenever adding or changing behaviour, or when asked to improve coverage.
---

# Write tests

## Pick the level

| If the test… | Marker |
| --- | --- |
| checks one class or function with collaborators faked, offline | `unit` |
| goes through several layers (CLI head → kernel → module → service), uses real files or subprocesses, offline | `integration` |
| installs Heinzelmännchen for real, uses the network or a real external service | `e2e` (set an explicit timeout) |

Set it once per file: `pytestmark = pytest.mark.unit`. The suite refuses tests with zero or several categories.

## Patterns

- Kernel with test modules: `make_kernel(builtins=True, example=module, example__sub=submodule)` (`__` → `.`);
  in plugins, `boot()` gives the kernel with the built-ins and every installed plugin.
- CLI: `CliRunner().invoke(build(kernel), ["example", "op", "--json"])`, then `json.loads(result.output)`.
- Services: construct directly, pass fakes as constructor arguments (e.g. `runner=` for subprocesses).
- Files: `tmp_path`; environment: `monkeypatch.setenv`. The `heinzel.testing` pytest plugin isolates
  `HZ_CONFIG_HOME`, `HZ_DATA_DIR` and `HZ_CACHE_DIR` and gives each test its level's timeout; never touch the real
  user configuration.

## Verification

- [ ] The test fails if you break the behaviour (try it once)
- [ ] `make test` passes with coverage ≥ 90 %
- [ ] Name states the behaviour; one behaviour per test
