---
status: accepted
date: 2026-10-23
---

# 0005: Dependency injection by type through ServiceLets

## Context and Problem Statement

Operations need services, services need settings, the kernel or other services. Global singletons make testing
hard; a DI framework is heavy and unfamiliar.

## Considered Options

- **Injection by type annotation through `ServiceLet`s** (chosen)
- **A DI library** (dependency-injector, lagom): more concepts than we need.
- **Passing the kernel everywhere**: hides real dependencies.

## Decision Outcome

Chosen option: **dependency injection by type through ServiceLets**, because operations stay plain functions and
tests can pass fakes.

- A module advertises `module.service(SomeService)` (optionally with a factory).
- `kernel.call(func, **given)` injects every parameter whose annotation is a provided type and wasn't given:
  `Kernel`, `Settings`, and all service types. Services are built lazily, once per kernel; their constructors are
  injected the same way.
- Two modules providing the same type is an error at boot.

### Consequences

- Good: operations read like plain functions; tests pass fakes as keyword arguments or build a kernel with test
  modules.
- Good: heads hide injected parameters from their interface automatically.
- Bad: cycles between services surface as recursion errors at first use; keep service graphs shallow.

### Confirmation

`tests/unit/kernel/kernel_test.py`: `test_injects_kernel_settings_and_services_once`,
`test_call_injects_only_missing_parameters`, `test_service_provided_twice_by_builtins_is_a_bug`,
`test_service_provided_twice_by_a_plugin_is_reported`.
