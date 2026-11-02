# AGENTS.md — kernel

Every module and plugin depends on this package; its public API is `hz` and `heinzel.kernel` (`__init__.py`). Treat a
change like a library release: an ADR for a new concept, unit tests for every branch. A file's docstring names its
purpose and ADR; read it before editing.

- A new let kind needs a consumer: a head, the kernel, a `system` submodule or the built-in module that owns the
  concept (`_lets.py`: `JobLet` → `system.daemon`, `PlaybookLet` → `workflow`, `ThemeLet`/`SignalLet` →
  `system.theme`, `SoundscapeLet` → `system.sound`, `StatusLet`/`ActionLet`/`WidgetLet` → `system.ui`, `SkillLet` →
  `system.skill`).
- Get secrets only through `Credentials` (ADR 0030).
- Modules get the read-only `CapabilityView`; `Capabilities` is kernel-internal. `Kernel.handle_for` gives the
  compiling module's jobs read handles only (ADR 0028).
- Adapters pass every string they return through `clean_text`; `redact_emails` keeps addresses out of prompts.
- `jsonable` refuses values with no JSON form; don't stringify them.
- Only `boot()` ties configuration, discovery and the kernel together.
- Themes and soundscapes are data plugins ship: pass values through the grammar and file checks in `_themes.py` and
  `_soundscapes.py` before they become CSS or audio; stream hosts come only from `STREAM_PROVIDERS` (ADR 0044, 0058).
- `ORG_SITE_PRECEDENCE` in `_layers.py` is the one private seam for the open org-site question (ADR 0040).
- Fields a project `hz.toml` can't set are listed in `ignored_in_project` (`_settings.py`).
- Chat agents only propose calls (`ActionGate`, `ProposedCall`); the person's confirmation runs them (ADR 0045).
- A module owns its session event vocabulary and projection; the kernel stores envelopes (`_session*.py`, ADR 0049).
- Declare `requires=` on modules, operations and jobs; mark a read-only operation `optional=True` if it degrades
  without its requirements (`_module.py`).
