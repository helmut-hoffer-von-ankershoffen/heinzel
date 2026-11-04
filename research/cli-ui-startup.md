# Direct UI command construction

## Current supervised readiness contract

On 2026-11-04 the owner explicitly increased the installed supervised web
readiness deadline from six to twelve seconds. This is a policy relaxation,
not a startup performance fix or a claim that the historical failures have a
proven cause. The same deadline applies to fresh starts, replacement exec and
cold recovery for both service-manager platforms. Exact ownership, a new
lifecycle instance, authentication and selected-profile checks remain required.
All other time budgets remain unchanged. The measurements and six-second
failures below describe the earlier contract and remain historical evidence.

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

## Follow-up: remove unused imports and duplicate input models

A later hosted supervised run on `69defc88b` still missed its unchanged six-second
readiness contract. The child accumulated 4.041 seconds of user CPU and 0.808
seconds of system CPU; its sampled import stack included FastAPI. That evidence
establishes startup work under load, not one proven slow function.

The follow-up keeps the existing CLI parser and full kernel/module discovery.
It imports setup, join, chat, command discovery and the interactive overview only
when those workflows run. Prompt support loads when an operation runs. The API
constructs one strict input model per private mutation and shares it between that
operation's normal and streaming routes, within one app build. Complete API
construction still finishes before publication. Constructing the web head's operation API creates 67
input models instead of 98; both builds register the same 323 API routes.

Two independent installed wheels used the same locked runtime dependencies and
CPython 3.14.8 on macOS. All 324 baseline Python files matched `69defc88b` exactly.
Before each process, bytecode was removed only from that disposable environment;
OS filesystem caches were not flushed. Each launch used empty private directories,
all built-in modules, no plugins, and update checks/native notifications disabled.
The process ran the real foreground UI command; the controller verified the built
HTML over HTTP and the authenticated lifecycle endpoint, then stopped its own
child with SIGINT. HTML hashes matched in all four runs.

| Order | HTTP ready wall (s) | Child user + system CPU (s) |
| --- | ---: | ---: |
| Baseline | 1.945 | 1.632 |
| Candidate | 1.767 | 1.601 |
| Candidate | 1.336 | 1.537 |
| Baseline | 1.393 | 1.576 |

The observed CPU reduction was 31–39 ms in these pairs. The wall difference also
contains scheduling and I/O variation and is not wholly attributed to the change.
This foreground comparison does not prove the hosted supervised deadline has
margin or that its failure is fixed.

An absent runtime record also used to construct a recursive JSON validator before
failing its private read. Reading first preserves every existing validation for a
present record. A separate local 100-call comparison constructed 100 validators
before and zero after, taking 18.4 ms versus 2.3 ms. This saves controller work;
it does not explain the child's recorded CPU time.

The immutable baseline fails the new unused-import and duplicate-model regressions;
the installed candidate passes. A representative private operation's complete
OpenAPI schema is byte-identical across the two wheels. Additional regressions
retain authentication, field bounds, unknown-field refusal, distinct app input
types, malformed runtime refusal, full CLI help/options and ordinary workflows.

## Follow-up: CLI presentation footprint

A later hosted run sampled daemon discovery with 2.331 seconds of child CPU over
6.101 seconds of wall time, with no runtime publication even after sampling. That
observation includes substantial waiting and a native read; it does not establish
that the sampled daemon observer is itself the cause.

The candidate defers 45 typed CLI renderer callbacks to their existing
implementations. Plugin documentation imports its page builder and recording
reader only when those operations use them. Full discovery and API construction
remain eager. The exact registered daemon observer class and its built-in service
identity checks are unchanged.

Comparing installed wheels against `33d3d6a19` after a complete kernel boot, direct
UI command construction and web app construction showed 18 implementation modules
absent: 16 CLI renderer modules, the documentation page builder and its file
helper. Those modules contain 84,259 source bytes. Including the typed delegation
functions, total loaded application source decreased from 3,202,331 to 3,131,420
bytes. Normal first-start bytecode writes decreased from 730 to 711.

| Interleaved cold foreground launch | HTTP ready wall (s) | Child user + system CPU (s) |
| --- | ---: | ---: |
| Baseline | 1.388 | 1.563 |
| Candidate | 1.640 | 1.591 |
| Candidate | 1.352 | 1.594 |
| Baseline | 1.409 | 1.578 |

These observations do not demonstrate an end-to-end timing improvement. The
established reduction is imported files, loaded source and bytecode writes. Each
launch used a disposable installed prefix with bytecode removed before launch,
empty private application directories, the real built HTML and authenticated
lifecycle checks. OS caches and host scheduling were not controlled.

All three complete OpenAPI schemas (public API, private API and mounted web API),
module identities, service factory identities and locked runtime versions matched.
A cold import guard fails on the immutable baseline and passes on the candidate.
Runtime annotation/signature checks and delegation tests cover all 45 callbacks;
the existing module and private API tests also pass.

A separate experiment disabled optional bytecode writes with the isolated Python
interpreter's `-B` flag. A normal fresh start took 1.549 seconds and created 730
cache files, followed by a 0.600-second cached start. The no-write sequence took
1.238 and 1.204 seconds and created no cache files. That first-start gain trades
away the next-start benefit on a fresh installation. No bytecode policy or
production launcher change is part of this candidate.
