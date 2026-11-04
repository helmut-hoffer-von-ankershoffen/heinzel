# Direct UI command construction

`hz ui` used to construct the schemas of every unrelated CLI operation before
Click dispatched its existing UI callback. Direct UI invocations now construct
the complete existing UI subtree and the same root options/logging callback.
Kernel boot and module/plugin discovery still run in full. Root help, version,
command discovery and other commands retain the complete CLI tree. When the UI
module is disabled, the full tree preserves plugin aliases occupying that path.
UI help also uses the full tree.

## Bounded local measurement

The baseline is commit `f7f094024`; the candidate changes only CLI construction.
A wheel was built and installed into each of two separate environments with
identical dependencies, CPython 3.14.8 on macOS. Each process had empty temporary
HOME/config/data/cache/state/log and daemon-unit directories, environment-only
credentials, and update checks/native notifications disabled. No owner config,
plugins, providers, pytest fixtures or application imports were preloaded.

The measured pair removed bytecode only in those two disposable environments
before launch; it did not flush the OS filesystem cache. A standard-library
`sys.setprofile` callback recorded source function entry/return, while
`threading.setprofile` observed publication. These times include profiler
overhead and are observations, not a benchmark distribution. Each process ran
`hz ui --port 0 --no-browser`; the controller fetched the actual built app,
verified HTTP 200 and HTML, then stopped that process with SIGINT. No live
service or sign-in was touched.

| Phase (seconds) | Before | After |
| --- | ---: | ---: |
| Kernel boot, including discovery | 0.7059 | 0.6618 |
| Discovery (within boot) | 0.6877 | 0.6435 |
| CLI build | 0.0967 | 0.0038 |
| Web app build | 0.3743 | 0.3739 |
| Publication entry since process start | 2.2578 | 2.0131 |
| Actual app HTTP 200 since process start | 2.3130 | 2.0594 |
| CLI operation schemas constructed | 166 | 4 |

The initial exploratory pair had warm baseline bytecode and cold candidate
bytecode and is excluded from the before/after comparison. The symmetric pair
above removes that confound. CLI construction dropped about 93 ms; total local
observed app availability improved about 254 ms, including variation outside
the changed phase. Only removal of unrelated CLI schemas is attributed to the
change. Web build was essentially unchanged.

This does not establish the cause of the hosted macOS failure, reproduce its
runner load, or prove margin under the managed UI six-second contract. This
local observation used foreground publication, not an installed supervised
service/control publication. The hosted managed readiness check remains
unchanged and must validate the composed candidate. No timing limit, worker
allocation, fixture or readiness definition changed.

Focused regressions compare complete UI command/option/help shapes, parsing
errors and device listing, ensure unrelated schemas are absent, check entrypoint
selection and preserve plugin aliases when UI is disabled. Independent source
review found no weakened test/check/threshold/deadline/gate, authentication,
configuration/profile or API contract; the alias fallback was independently
reproduced before approval.
