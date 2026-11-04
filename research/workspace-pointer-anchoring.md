# Page loading during a workspace pointer press

The original macOS ARM failure is retained in CI run 41832979583, job 126584868207, at source e04a861c4.
`test_mounted_scheduled_connected_read[True-webkit]` timed out waiting for a completed connected-read event inside
its closed tool-activity disclosure. The existing disclosure trace identifies a lost pointer activation, rather
than a duplicate hidden locator or a replaced component:

- At 311 ms, pointerdown hit the original connected summary at y=255.7; its details were closed.
- At 339 ms, a page mutation moved that same summary to y=1365.95; its identity and closed state were unchanged.
- At 340 ms, pointerup hit a table cell, and click hit an ancestor div. The summary never received activation.
- At 497 ms, scrolling restored the summary to y=255.95, after the physical release had already missed it.

The selected Chat panel and its ancestors remained visible and non-inert. The hidden completed event was therefore
the consequence of the closed disclosure. Changing `.first()`, synthesizing activation or extending its thirty-second
deadline would not repair the product behavior.

The shared workspace anchor previously reconciled page height only when ResizeObserver delivered a rendering
notification. DOM changes can become observable to pointer hit testing before that notification. The anchor now
also reconciles page DOM mutations at their microtask checkpoint, retaining rendering notifications for image, font
and CSS size changes. It preserves viewport reflow and intentional navigation/scroll behavior, releases removed
interaction targets and closed workspaces, and disconnects both observers on cleanup.

The deterministic browser regression retains the real delayed doctor response and physical summary press/release.
It holds only the page-height ResizeObserver delivery until after pointer release to reproduce the observed ordering;
DOM layout, event hit testing and application data remain real. The original connected-read, revocation, durable
state, deduplication and thirty-second assertions remain in place, including the separate late-revocation journey.

The new WebKit regression fails against the original source in 10.66 seconds: page growth moved the pressed summary
to y=1305.95, pointerup hit a table cell and the disclosure stayed closed, matching the hosted failure. The same
regression passes with the repair in 8.92 seconds. Fifteen focused anchor tests pass, including mutation-before-resize,
native scroll compensation, viewport reflow, Page interaction, scroll-away, removed control, hidden/disconnected
workspace and observer cleanup. `make lint typecheck` passes, including import contracts, generated API/documentation
freshness and Python/frontend types. Fresh hosted CI remains required; these results are not release acceptance.
