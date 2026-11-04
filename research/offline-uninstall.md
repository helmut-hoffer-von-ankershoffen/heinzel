# Offline tool removal boundary

The new explicit `hz system uninstall` dispatch enters before kernel boot and
background release checks. Its help does not inspect an installation. Removal
constructs an empty kernel for user-scope configuration and progress; it does
not discover plugins, fetch packages or require the web UI to be running.

The implementation requires the existing standard application's recorded
installation continuity, physical uv tool destination and receipt-owned script
links. It uses only an existing owned uv executable with `--offline`,
`--no-config` and `--no-python-downloads`. A private lifecycle/tick fence and the
existing durable installation gate cover the mutation. No failed removal
attempt invokes a network restore or reports success. User configuration and
data are retained.

Owned web removal reuses its existing exact unit, launcher, profile and loaded
manager checks, independently of the preview flag. Daemon removal compares its
exact generated unit and install record, loaded argv and actual manager
quiescence before authorizing tool removal. Foreign loaded labels and altered
files refuse. Integration cleanup is conditional on observed tool removal.

Nine focused tests currently exercise the offline command shape, selected tool
and script removal with unrelated data retained, changed script/receipt/prefix
and missing continuity refusals, uncertain uv outcomes, and pre-discovery CLI
help/dispatch. These use an injected removal runner, not a real installed
uninstall. The source also passes mypy and Ruff.

This is an incomplete implementation checkpoint, not release evidence. Required
follow-up is a real installed positive/negative fixture under OS-enforced
outbound-network denial, owned manager cleanup and failure tests on both
platforms, generated command documentation, and independent installer/security
review. No host jobs or user installation have been touched.

Unmarked SDK/uv environments and desktop-managed runtimes currently refuse.
The new uv executable check also refuses group-writable executable ancestors,
including some Homebrew Cellar layouts; supported handling needs resolution
before publication. Interrupted removal leaves a local recovery gate rather
than guessing whether a partial uninstall can be reversed.
