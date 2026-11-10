#!/usr/bin/env bash
# Execute only in an isolated disposable checkout of the reviewed candidate. No publication authority.
set -euo pipefail
base=b234067b172f33db59f98c9ea38808b3d5ab9bc5
head=628f2e0501623d785c16a5c8cb7e71b0c9b162a8
tree=d72c0eb84f24637761b91c65b5aa5fab426e6ca4
[[ $(git rev-parse HEAD) == "$head" && $(git rev-parse HEAD^{tree}) == "$tree" ]]
expected=$'scripts/related_tests.py\ntests/unit/scoped_checks_test.py\nweb/e2e/chat-ready.mjs\nweb/e2e/device-reopen.mjs\nweb/e2e/generic-agent-chat.mjs'
[[ $(git diff --name-only --no-renames "$base" "$head") == "$expected" ]]
[[ -z $(git status --porcelain) ]]
: "${GITHUB_RUN_ID:?actual Actions run required}" "${GITHUB_RUN_ATTEMPT:?actual Actions attempt required}" "${RUNNER_TEMP:?runner temporary directory required}"
mkdir -p "$RUNNER_TEMP/focused-650"
printf 'base=%s\nhead=%s\ntree=%s\nrun=%s\nattempt=%s\njob=%s\n' "$base" "$head" "$tree" "$GITHUB_RUN_ID" "$GITHUB_RUN_ATTEMPT" "${GITHUB_JOB:?}" > "$RUNNER_TEMP/focused-650/identity.txt"
case "${1:?python or browser}" in
  python)
    uv sync --frozen --no-build --no-dev --group test
    uv run --no-sync pytest -n 2 tests/unit/scoped_checks_test.py tests/unit/scripts_test.py --junitxml="$RUNNER_TEMP/focused-650/results.xml" ;;
  browser)
    make e2e-prepare
    uv run --frozen pytest -m e2e tests/e2e/web_ui_test.py::test_a_paired_browser_reopens_signed_in_without_a_link_after_hz_ui_restarts tests/e2e/web_ui_test.py::test_the_apps_own_restart_keeps_a_paired_browser_signed_in tests/e2e/generic_agent_chat_test.py tests/e2e/connected_read_test.py tests/e2e/scheduled_connected_test.py tests/e2e/scheduled_notification_review_test.py tests/e2e/source_suggestion_test.py --junitxml="$RUNNER_TEMP/focused-650/results.xml" ;;
  *) exit 2 ;;
esac
