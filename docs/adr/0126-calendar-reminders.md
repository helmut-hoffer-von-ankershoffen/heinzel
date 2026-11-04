---
status: proposed
implementation: partial
---

# 0126: Calendar reminder scheduling

Calendar reminders need a deadline-aware scheduler independent of the daemon's configurable wall-clock tick. A five-minute tick is not a five-minute warning: an event may start between ticks, the machine may sleep, and Linux intentionally delays a timer. The hourly notification watcher is unsuitable too. A worker therefore refreshes the bound provider at least every 30 seconds and wakes at the next five-minute boundary. It refreshes again before recording, and delivery revalidates against the current provider. A resumed machine must not issue an alert after its boundary. Delivery remains subject to quiet hours, notification consent and operating-system availability; sleep, slow providers, process startup or OS delivery can make an alert late or prevent it.

Calendar reminders are disabled by default. The user explicitly enables the user-scoped notification setting and separately installs a user service. Project configuration cannot grant calendar consent. The worker uses the registered agenda operation and the generic calendar capability, not a particular connector. It periodically exits so the OS restarts it with the installed code; no long-lived process is installed automatically. A user may uninstall the service. Process and installation handover, service manager failures and sleep behavior require independent platform review and isolated lifecycle testing.

Each selected occurrence is identified by its provider, occurrence ID, actual start and end instants, and boundary kind. The existing notification store atomically writes a replay key and notice. Version changes withdraw pending notices; unavailable providers fail closed. Expiry is exclusive at the meeting boundary, including for notices held by quiet hours or rate limits. Web and native delivery re-read before offering or claiming a notice, although an external event can change immediately after a successful read; no polling scheme can make that provider-side race atomic. Concurrent delivery checks also honor withdrawal and expiry under the notification lock.

Replay keys are currently retained by the existing durable notification contract. Their unbounded retention needs a crash-safe compaction design before treating high-volume long-running use as complete. The first implementation must also verify the platform supervisor lifecycle, process startup after system updates, native and web delivery, consent revocation and the full calendar UI in isolated fixtures. These are acceptance requirements, not evidence supplied by a configuration setting or a unit test.
