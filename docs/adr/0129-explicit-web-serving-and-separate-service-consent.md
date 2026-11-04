---
status: proposed
date: 2026-11-04
implementation: partial
decision-makers: maintainer and independent security reviewer
amends: 0110, 0117
amends-0110: offer separate opt-in services through the verified managed application
amends-0117: make web installation available without a preview flag and require explicit serving
related: 0020, 0046, 0108, 0115
---

# 0129: Explicit web serving and separate service consent

## Context and Problem Statement

The bare web command starts a listener even when a person is trying to discover its service commands. The
versionless launcher already establishes a verified persistent standard installation, but offers no distinct
choice for scheduled Heinzels and web availability after login.

## Considered Options

- **Explicit serving and separate opt-in service choices** (chosen).
- Continue starting the listener on bare group use and require a preview flag for service installation.

## Decision Outcome

Chosen option: **explicit serving and separate opt-in service choices**, because discovery must be inert and
persistence must reflect a person's separate choices.

- Bare `hz ui` shows group help without starting or attaching to a server. `hz ui serve` explicitly serves in the
  foreground. `hz ui open` opens the owned existing web service, or serves in the foreground if none is installed.
  It never falls back to another listener when an existing service cannot be verified.
- `hz ui install` and owned startup, attachment and restart no longer require the `persistent_web` preview flag.
  Existing installations, manager ownership, profile routing, mutation gates and authentication remain checked.
  Exact owned launchers from the published service format retain their supervised entry path; unowned or changed
  launcher bytes remain refused. New launchers use `ui serve`. Published updater trial arguments and authenticated
  inherited-socket resumes remain accepted internally, so an older running app can safely update into this CLI.
  The retired flag is harmless in older configuration files; other unknown flags remain diagnostic errors.
- Bare interactive `uvx heinzel@latest` asks independently whether to install the scheduled daemon, the web
  server after login and the native notifier. All three interactive choices default to yes; no noninteractive
  invocation silently selects them. A daemon choice never implies notifier consent. It first establishes or verifies the persistent standard app
  with the supported installer, then runs selected service commands in that verified managed interpreter.
  Offline CLI command discovery retains the published foreground entry point for verified older standard apps;
  unsupported service choices are refused before any service mutation. Daemon installation no longer implies
  companion installation; explicit `--notifier` or the separate notifier choice retains that capability.
  The temporary bootstrap interpreter never becomes a service target. A failed install or verification stops
  launch; no success is inferred from a subprocess return code alone.
- First interactive setup asks for missing name and team name plus the actual profile health requirements.
  Existing values are preserved and every write uses the managed ConfigService operations. It invents no profile
  values or optional credentials. Interactive setup is completed only after the actual managed doctor reports up;
  genuinely degraded or failed services, installation checks and corruption remain failures.
- The selected notifier uses the managed application and existing companion verification. Unavailable platforms
  and desktops report unavailability; a request handed to the OS never establishes permission or visible delivery.
- Help, explicit runtime arguments, machine-readable calls and noninteractive use ask no questions. Declining
  a choice performs no service installation or removal. Existing services are preserved.
- This changes the following release's source only. Published artifacts remain unchanged. Real platform and
  provider acceptance remains a release requirement, distinct from isolated fixture validation.

### Consequences

- Good: help is safe, foreground serving is explicit, and each persistent service has separate consent.
- Bad: the launcher has additional consent and managed-command verification paths to maintain.

### Confirmation

Focused CLI and bootstrap tests enforce inert help, three separate interactive default-yes choices, prompt-free automation and
verified managed delegation. Installed service tests retain profile, authentication, ownership and restart assertions.
