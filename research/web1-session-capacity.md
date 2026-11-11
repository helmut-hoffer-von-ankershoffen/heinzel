# Browser lane capacity at the 600-second step boundary

## Observed first-attempt failure

Frozen Core source `190f73e179e559e63f618927950c435144f50fa2`,
[full CI 41996546993](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/runs/41996546993),
[macOS ARM web-1 job 127141963316](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/runs/41996546993/job/127141963316)
contains 56 case starts and 56 teardown reports, with no unmatched case start or
named test failure. The immediate `E2E end` marker is emitted by the controller
when it receives a teardown report; it does not prove pytest or a worker exited.

The `scripts/steps.py` command starts at 08:55:48.650 UTC. First test starts are
08:55:56.885. The final three teardown reports are:

| Time UTC | Case | Reported phase seconds |
| --- | --- | ---: |
| 09:05:23.567 | Mobile share-WebKit section | 13.809 |
| 09:05:35.452 | Calendar panel | 20.281 |
| 09:05:46.064 | Settings search font floor | 22.489 |

The outer step terminates at 09:05:49.376 with Error 124. Its 600-second timer
starts before pytest startup and owns group termination. Only 3.312 seconds
separate the final teardown report and termination. No captured worker-exit,
controller-session-finish, or process snapshot identifies the remaining work.
The trailing `[100%]` is not a successful session result.

Reported phase durations total 1167.180 seconds on two workers; first test start
to last teardown report is 589.179 seconds. Even ideal division of the recorded
phase work requires 583.590 seconds, before startup and shutdown. This lane had
little capacity for normal overhead, without a demonstrated test hang.

In the same run,
[macOS ARM web-2 job 127141963459](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/runs/41996546993/job/127141963459)
passed 18 cases in 128.29 seconds. Its phase sum is 236.583 seconds. Its final
teardown report at 09:00:18.673 precedes the summary at 09:00:23.303 by 4.630
seconds. That observed normal shutdown interval exceeds web-1's remaining
3.312 seconds; it is supporting context, not proof of the precise web-1 exit
path.

## Capacity correction

The previous browser allocation alternated whole filenames alphabetically. That
put the large `web_ui_test.py` sweep alongside seven other files in web-1.
The new allocation gives that complete file (29 cases) to existing web-1 and
every other browser file (45 cases) to existing web-2. The recorded work is
divided as follows:

| Allocation | web-1 phase seconds | web-2 phase seconds |
| --- | ---: | ---: |
| Previous alternating filenames | 1167.180 | 236.583 |
| Mounted UI sweep / remaining browser files | 689.756 | 714.007 |

These are sums of the original recorded durations regrouped by file, not new
execution results or a wall-clock guarantee. No test body, node ID, marker,
worker count, CI cell, per-test deadline, 600-second session/step deadline, or
15-minute job deadline changes. Existing supervised and CLI allocations remain
separate. The full collected-node partition guard still proves every original
case is selected exactly once, and browser-runtime preparation remains required.
The allocation policy assertion fails against the previous Makefile and passes
against the corrected allocation. Hosted execution of the new allocation is
still required. This capacity correction does not diagnose or fix the separate
README guardian SIGINT failure.

## Smallest next hosted study

Use at most four independent hosted ARM jobs: the two changed browser lanes,
two fresh replicas each, one attempt per job. Bind all jobs to the independently
reviewed candidate. Execute the actual `make test-e2e SUITE=web-1` or
`make test-e2e SUITE=web-2` command with the original `VERBOSE=1`, original pinned
bootstrap, browser preparation, worker count, and all original limits. Direct
pytest omits the outer step guard and is not an equivalent experiment.

An observation-only pytest plugin should record bounded fixed-label events for
worker/controller configure, completion of the outer runtest protocol, session
finish entry/exit, worker-down, and unconfigure. Record each worker's collected
inventory hash and completion counts, rather than assuming an early paired case
means the complete inventory finished. Hook wrappers must yield normally and
must not change reports, ordering, selection, deadlines, or signal handlers.

A launcher may observe only its newly created make process and descendants,
using PID plus creation time and ancestry to avoid adopting unrelated processes.
Record fixed executable roles, process states, and relative times; never argv,
environment, URLs, profile contents, or raw exception text. It adds no signals
or cleanup policy; the actual step wrapper retains ownership of group cleanup.
Take low-frequency bounded snapshots and one after the final teardown marker.
Preserve original return codes and failed-job artifacts. Missing milestones or
artifacts remain explicit unknowns. Review a concrete observer/workflow before
dispatch. These diagnostic jobs do not replace normal required CI or acceptance.
