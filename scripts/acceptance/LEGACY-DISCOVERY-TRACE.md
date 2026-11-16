Run the published 0.9.50 case 6 discovery diagnostic on a disposable hosted
macOS runner. From an authorized, independently reviewed diagnostic branch:

```sh
sm ci one dispatch fix/legacy-discovery-diagnostic --kind pytest \
  --test tests/integration/legacy_discovery_trace_hosted_test.py --os macos-latest \
  --throwaway --wait --cleanup
```

This runs case 6 once with the original Core wheel, profile and installer
hashes. All acceptance assertions still run. After verifying the published
0.9.43 bridge and its installed console entry point, the diagnostic enters the
same module with the owned Python 3.12 interpreter and traces that invocation.
It leaves the launcher and wheel untouched. The diagnostic suppresses site
startup hooks and ignores existing bytecode. Its result is never acceptance.

The private trace and sanitized hosted artifact contain up to 16 events
from the owning discovery calls. They hold only operation and event enums,
numeric HTTP status or release and Python version values. A missing trace
means the bridge call was unavailable or failed before tracing. A trace
return of null can follow either a normal null return or an exception exit.
Read a nearby exception event to distinguish them. The old acceptance
rejection remains the original evidence.
