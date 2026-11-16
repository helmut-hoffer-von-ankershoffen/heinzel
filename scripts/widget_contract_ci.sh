#!/bin/bash
set -euo pipefail

case "${1:-}" in
  build)
    [[ "${FOCUSED_REPEAT:-}" == 1 && "${FOCUSED_STRESS:-}" == false ]]
    fixture="${RUNNER_TEMP:?}/widget-contract"
    mkdir -m 700 "$fixture" "$fixture/home" "$fixture/cache" "$fixture/tmp" "$fixture/bin" focused-artifacts
    export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
    git rev-parse HEAD HEAD^{tree} > focused-artifacts/widget-source.txt
    sw_vers > focused-artifacts/widget-macos.txt
    xcodebuild -version > focused-artifacts/widget-xcode.txt
    xcrun swiftc --version > focused-artifacts/widget-swift.txt
    node --version > focused-artifacts/widget-node.txt
    openssl version > focused-artifacts/widget-openssl.txt
    shasum -a 256 apple/Shared/{NativeRoute,ActivityCatalog,ActivityWidgetData,WidgetCredentialStore,WidgetFencedEnrollment,WidgetEnrollmentTransport}.swift apple/tests/{WidgetFencedProtocolHarness,WidgetWebKitEnrollmentTests}.swift tests/integration/web_widget_{apple,webkit}_contract_test.py > focused-artifacts/widget-source-sha256.txt
    xcrun swiftc -parse-as-library -swift-version 6 apple/Shared/{NativeRoute,ActivityCatalog,ActivityWidgetData,WidgetCredentialStore,WidgetFencedEnrollment}.swift apple/tests/WidgetFencedProtocolHarness.swift -o "$fixture/bin/fenced"
    xcrun swiftc -parse-as-library -swift-version 6 apple/Shared/{NativeRoute,ActivityCatalog,ActivityWidgetData,WidgetCredentialStore,WidgetFencedEnrollment,WidgetEnrollmentTransport}.swift apple/tests/WidgetWebKitEnrollmentTests.swift -o "$fixture/bin/webkit"
    codesign --force --sign - "$fixture/bin/fenced"
    codesign --force --sign - "$fixture/bin/webkit"
    codesign --verify --strict --verbose=2 "$fixture/bin/fenced"
    codesign --verify --strict --verbose=2 "$fixture/bin/webkit"
    shasum -a 256 "$fixture/bin/fenced" "$fixture/bin/webkit" > focused-artifacts/widget-binary-sha256.txt
    {
      echo "HOME=$fixture/home"
      echo "XDG_CACHE_HOME=$fixture/cache"
      echo "TMPDIR=$fixture/tmp"
      echo "HZ_QUEUE_STATE=$fixture/queue-state"
      echo "HZ_WIDGET_FENCED_HARNESS=$fixture/bin/fenced"
      echo "HZ_WIDGET_WEBKIT_HARNESS=$fixture/bin/webkit"
      echo "HZ_WIDGET_ENROLLMENT_SOURCE=${GITHUB_WORKSPACE:?}/apple/Shared/WidgetEnrollmentTransport.swift"
    } >> "${GITHUB_ENV:?}"
    ;;
  verify)
    python3 -c 'import sys, xml.etree.ElementTree as E; cases=E.parse("focused-artifacts/junit.xml").findall(".//testcase"); expected={"test_cancelled_issue_race_replans_and_replays_revoke", "test_native_fenced_retry_after_lost_core_reply", "test_native_script_and_core_recover_lost_reply", "test_real_webkit_cancelled_lost_issue_reply"}; actual=[c.get("name") for c in cases]; ok=len(cases)==4 and set(actual)==expected and all(not any(c.find(x) is not None for x in ("skipped", "failure", "error")) for c in cases); print("widget contracts:", actual, "PASS" if ok else "FAIL"); sys.exit(0 if ok else 1)'
    ;;
  *) echo 'usage: widget_contract_ci.sh {build|verify}' >&2; exit 2 ;;
esac
