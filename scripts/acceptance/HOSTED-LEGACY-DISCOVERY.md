# One disposable legacy discovery diagnostic

This opt-in adapter uses the existing `focused-job.yml` route. It runs unchanged literal published case 6 once. Only its genuine `the published bridge updates to the target release` failure starts the reviewed instrumented probe against the retained installation, before the disposable VM disappears. It never changes the original status, target, resolver inputs or acceptance assertions. A passing partial case is not complete release acceptance, and a successful follow-up cannot reverse a rejection.

After independent exact-head review and coordinator authorization, push the branch normally. From this checkout, the coordinator can dispatch one separate validation SHA:

```sh
sm ci one dispatch fix/legacy-discovery-hosted --test tests/diagnostics/legacy_discovery_test.py --kind pytest --os macos-latest --repeat 1 --throwaway
```

Do not dispatch directly on a PR head, retry the run, or run this selector locally. The adapter refuses anything except the first attempt of this exact unstressed hosted macOS selector, verifies checkout bytes against `GITHUB_SHA`, and writes exclusive intent files. Existing admission and runner-capacity guards still apply.

The workflow's `focused-pytest-macOS-<run-id>` artifact contains `legacy-discovery/` with:

- `diagnostic-intent.json`: exact dispatch source SHA and source hashes.
- `original-case6.json`: original sanitized case record, saved before any diagnostic mutation.
- `original-result.json`: the unchanged harness's final partial/rejected receipt.
- `probe-intent.json` and `discovery-probe.json`: one separate sanitized, instrumented non-acceptance observation; otherwise `probe-error.json` or `probe-not-run.json` records why none is available.

The adapter removes only disposable Core bytecode and copies trusted runner uv executables and the original service-manager refusal spies into the private bin directory after saving the original failure. It passes an allowlisted private environment without GitHub credentials. The follow-up runs the same installed published updater, but is a **second** network observation: network conditions may have changed. Its isolated entry point, output capture and instrumentation also differ from the first update. It can establish a failure category observed during that follow-up, not retrospectively prove the earlier failure's cause. Missing events are not proof that discovery succeeded. Raw CLI and child output are discarded rather than uploaded.

No workflow, public acceptance harness, release receipt or target is modified. The focused runner's 1080-second ceiling includes setup, original case and follow-up; the probe's own 900-second budget therefore does not guarantee completion. A timeout may leave only the pre-probe original case record and intent; the hosted VM must then be destroyed normally. This does not authorize another dispatch. Physical devices and the live Mini are outside this diagnostic.
