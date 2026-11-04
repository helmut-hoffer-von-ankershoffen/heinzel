---
status: proposed
date: 2026-11-04
implementation: partial
---

# 0126: Calendar reminder scheduling

Calendar reminders need a deadline-aware scheduler independent of the daemon's configurable wall-clock tick. A five-minute tick is not a five-minute warning: an event may start between ticks, the machine may sleep, and Linux intentionally delays a timer. The hourly notification watcher is unsuitable too. A worker therefore refreshes the bound provider at least every 30 seconds and wakes at the next five-minute boundary. It refreshes again before recording, and delivery revalidates against the current provider. A resumed machine must not issue an alert after its boundary. Delivery remains subject to quiet hours, notification consent and operating-system availability; sleep, slow providers, process startup or OS delivery can make an alert late or prevent it.

Calendar reminders are disabled by default. The user explicitly enables the user-scoped notification setting and separately installs a user service. Project configuration cannot grant calendar consent. The worker uses the registered agenda operation and the generic calendar capability, not a particular connector. It periodically exits so the OS restarts it with the installed code; no long-lived process is installed automatically. A user may uninstall the service. Process and installation handover, service manager failures and sleep behavior require independent platform review and isolated lifecycle testing.

Each selected occurrence is identified by its provider, occurrence ID, actual start and boundary kind; an end reminder also includes the end instant. Changing only an event's end therefore does not repeat its start reminder. The existing notification store atomically writes a replay key and notice. Version changes withdraw pending notices; unavailable providers fail closed. Expiry is exclusive at the meeting boundary, including for notices held by quiet hours or rate limits. Web and native delivery re-read before offering or claiming a notice, although an external event can change immediately after a successful read; no polling scheme can make that provider-side race atomic. Concurrent delivery checks also honor withdrawal and expiry under the notification lock.

Deadline-scoped replay receipts are compacted after a persisted time watermark passes their exclusive expiry; a fixed capacity fails closed if too many remain outstanding. A backward clock adjustment cannot replay a receipt whose expiry has already passed the watermark. The first implementation must also verify the platform supervisor lifecycle, process startup after system updates, native and web delivery, consent revocation and the full calendar UI in isolated fixtures. These are acceptance requirements, not evidence supplied by a configuration setting or a unit test.

The service captures the selected user and explicit configuration paths and resolved state, data, cache and log directories. Its supervisor identity is derived from those locations; it never shares a global label across profiles or copies secrets or consent into the unit. The worker verifies that binding before reading a calendar. Installation, status and removal use one captured selection throughout each action. A setup retry can load an unchanged, user-owned unit after bootstrap failure; removal accepts an explicit supervisor observation that the unit was never loaded. Modified, linked, foreign-owned or group/world-writable definitions remain untouched.

Calendar offers, claims and native presentation refresh policy and time after provider validation, including exclusive expiry under the store lock. Policy edits update consent, quiet mode, native delivery, working hours and time zone without waiting for a worker restart. A profile with working hours 07–22 in Europe/Berlin therefore suppresses reminders during 22–07; a reminder does not survive its meeting boundary to be shown the next morning. The browser checks expiry again after service-worker registration and the cross-tab sound lock, so delayed registration or tab suspension cannot revive an expired meeting reminder.

The authenticated desktop web head offers profile-specific setup and removal through the app's confirmation dialog. Confirmation describes login persistence and the separate background-read consent; cancellation performs no operation. The public API and MCP cannot install or remove this worker. Calendar never sends the person to a terminal to finish setup. Unit tests use fake supervisors and clocks; they do not establish actual launchd/systemd, native OS or provider acceptance.

ADR 0133 composes the worker with offline SDK removal. Setup and each worker cycle honor an SDK-wide gate and
SDK/profile lifecycle locks before resolving notification or agenda services. Every recorded profile's exact owned
unit joins provider-free maintenance inventory; removal stops all workers before deleting the SDK and keeps their
definitions for copied recovery until the SDK is observed gone. Consent and replay/deduplication history remain.
