# Inherited capture listener

A source-reviewed gallery bootstrap can use
`heinzel.heads.web.capture.serve_capture_fixture` on macOS or Linux. Run
the bootstrap inside the existing OS network fence with the fresh private
capture profile as both its home and working directory. Pass the selected
unprivileged port separately from the inherited app descriptor.

The launcher keeps its exclusive IPv4 loopback listener open through teardown.
The child adopts its copy only after checking the exact address, port, stream
type, listening state, and disabled reuse options. It marks that copy
close-on-exec. An invalid descriptor stays with its original owner. Listener
identity relies on the launcher retaining the exclusive reservation and passing
only the intended descriptor. The API does not authenticate arbitrary code
already running in the child.

The entry point checks that normal service config reads see only the fixture's
user scope. It refuses existing site, project, or explicit config. It expects the
capture toolkit's isolated directories and environment credential backend,
disabled update checks, and no external UI origins. It serves Core's regular
ASGI app and reports its private session URL after startup. It starts no
automatic updater and exposes no restart callback.

The normal CLI remains the production entry point. The fixture entry point
requires an inherited listener and refuses restart handoff state. It cannot
replace the network fence. The launcher and the reviewed plugin fixture still
own that boundary and must exclude real provider credentials.
