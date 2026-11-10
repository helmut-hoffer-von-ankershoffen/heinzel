#!/usr/bin/env bash
# Execute only in an isolated disposable checkout of the reviewed candidate. No publication authority.
set -euo pipefail
base=de5ebbb5b471a477c2c875e4e1effc501d021642
head=9f26091bbf84ef7141fea0b0454008a6344b84d9
tree=d7aca1ead0f18deab1c836d2cc0aaf124cc473c6
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
    uv run --frozen pytest -m e2e tests/e2e/web_ui_test.py::test_a_paired_browser_reopens_signed_in_without_a_link_after_hz_ui_restarts tests/e2e/web_ui_test.py::test_the_apps_own_restart_keeps_a_paired_browser_signed_in tests/e2e/generic_agent_chat_test.py tests/e2e/connected_read_test.py tests/e2e/scheduled_notification_review_test.py tests/e2e/source_suggestion_test.py --junitxml="$RUNNER_TEMP/focused-650/results.xml" ;;
  *) exit 2 ;;
esac
python3 - "$RUNNER_TEMP/focused-650/results.xml" "$GITHUB_WORKSPACE/bootstrap/research/focused-650/$1-collection.txt" <<'PYCODE'
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
root = ET.parse(sys.argv[1]).getroot()
cases = list(root.iter("testcase"))
actual = [case.attrib["classname"].replace(".", "/") + ".py::" + case.attrib["name"] for case in cases]
expected = Path(sys.argv[2]).read_text().splitlines()
if len(actual) != len(expected) or set(actual) != set(expected) or any(
    case.find("skipped") is not None or case.find("failure") is not None or case.find("error") is not None
    for case in cases
):
    raise SystemExit("Exact frozen test collection did not pass without skips")
print(f"All {len(actual)} frozen test cases passed")
PYCODE
