# AGENTS.md — kernel

Every module and plugin depends on this package; its public API is `hz` and `heinzel.kernel` (`__init__.py`). Treat a
change like a library release: an ADR for a new concept, unit tests for every branch.

- `_module.py` — `Module` and its decorators (`operation`, `job`, `service`, `settings`, `info`, `health`,
  `credential`, `provides`; `requires=` on modules, operations and jobs).
- `_protocols.py` — integration capability protocols (`CodeHost`, `Tracker`, `Documents`, `Chat`, `Meetings`,
  `Calendar`) and their models; `_text.py` — `clean_text`, which adapters apply to every string they return;
  `_agents.py` — the `AgentRunner` protocol and `AgentResult` (ADR 0029; adapters live in `system.runner`);
  `_capabilities.py` — the capability registry with each method's mode and requirement strings;
  `_bindings.py` — `[capabilities]` bindings; `Capabilities` (kernel-internal) hands requesters mode-gated
  handles, modules get the read-only `CapabilityView` (ADR 0028); `Kernel.handle_for` gives the compiling module's
  jobs read handles only.
- `_credentials.py` — `Credentials`, the only way to secrets (ADR 0030), and the environment backend.
- `_lets.py` — let types, `Check`/`Status` and `CatchUp`. A new let kind needs a head, the kernel or a `system`
  submodule that consumes it (`JobLet`: `system.daemon`).
- `_jobs.py` — `CompiledJobs`, a service, not a let: jobs compiled from data (workflow contracts, ADR 0021);
  `Kernel.compiled_jobs()` compiles them afresh on every call, and `system.daemon` runs them too.
- `_discovery.py` — package walk + `heinzel.modules` entry points; plugin failures are collected, built-in ones raise.
- `_kernel.py` — registration (pluggy), disabled modules, `lets()`, injection (`call`, `resolve`, `bind`, `available`).
- `_boot.py` — `boot()`, the only place that ties configuration, discovery and the kernel together.
- `_config.py`, `_settings.py` — layered TOML + env configuration (`ignored_in_project`: fields a project
  `hz.toml` can't set); `_installation.py` — the uv tool environment;
  `_progress.py` — head-agnostic progress; `_duration.py` — `parse_duration` (`5m`); `_release.py` —
  `ReleaseCache` and `ReleaseSource`, the latest release last seen and who asks for it (ADR 0031).
