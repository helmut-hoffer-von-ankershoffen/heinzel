---
status: proposed
date: 2026-10-25
decision-makers: maintainer
related: 0003, 0018, 0056
---

# 0074: Machine resource meters use bounded local sampling

## Context and Problem Statement

The maintainer wants compact CPU, memory and load meters in the footer, with the same information available through
the CLI and MCP. These are machine measurements, rather than the current Python process's consumption. macOS and
Linux expose different native interfaces; a UI must not run platform shell commands or interpret unavailable data
as a healthy zero.

## Considered Options

- **A system.resource operation using psutil** (chosen): one typed, cross-platform local measurement service.
- **Native commands or handwritten platform bindings**: more platform parsing and maintenance for three measurements.
- **Browser measurements**: do not describe the host running hz and are unavailable to other heads.

## Decision Outcome

Chosen option: **a system.resource operation using psutil**, because all heads then view the same bounded local
sample. Pin psutil 7.2.2 (BSD-3-Clause) and include its attribution. Its supported CPU/memory interfaces are documented
by the [project](https://psutil.io/) and [release](https://pypi.org/project/psutil/7.2.2/).

- `hz system resource` returns machine CPU and memory percentages, one-minute load, detected logical cores and an
  observation timestamp. Missing measurements are null with an explanation, never fabricated zero values.
- One service serializes sampling and caches it for one second. Each uncached CPU measurement requests one 100 ms
  interval, including its first request on a new thread. Operating-system scheduling adds overhead; no hard
  wall-clock deadline is claimed. No independent permanent sampler is introduced. The UI polls only while visible.
- CPU/memory approach their maximum at 90 percent. Load is normalized by detected logical cores; above twice that
  count is red. Colour is accompanied by accessible names, values and details.
- This operation does not replace Doctor. A separate health indicator consumes the existing Doctor report and
  links to its details; rendering either indicator does not start a live connector check.

### Consequences

- Good: measurements are consistent across heads and independent of browser/device capabilities.
- Good: a cached sample limits work when several placements or clients ask concurrently.
- Bad: one small native dependency is added; unsupported platform measurements remain explicitly unavailable.

### Confirmation

`tests/unit/modules/resources_test.py` exercises sampling, cache expiry, concurrency, missing measurements and strict
JSON values. The web meter tests cover thresholds and accessible values, including load above twice the core count.
