# AGENTS.md — kernel

Every module and plugin depends on this package; its public API is `hz` and `heinzel.kernel` (`__init__.py`). Treat a
change like a library release: an ADR for a new concept, unit tests for every branch.

- `_module.py` — `Module` and its decorators (`operation`, `service`, `settings`, `info`, `health`).
- `_lets.py` — let types and `Check`/`Status`. A new let kind needs a head (or the kernel) that consumes it.
- `_discovery.py` — package walk + `heinzel.modules` entry points; plugin failures are collected, built-in ones raise.
- `_kernel.py` — registration (pluggy), disabled modules, `lets()`, injection (`call`, `resolve`, `bind`, `available`).
- `_boot.py` — `boot()`, the only place that ties configuration, discovery and the kernel together.
- `_config.py`, `_settings.py` — layered TOML + env configuration; `_installation.py` — the uv tool environment;
  `_progress.py` — head-agnostic progress.
