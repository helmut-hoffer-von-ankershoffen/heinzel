# Electron packaging against the repaired core

The Electron shell packages on Apple silicon with the pinned build tools and retains its external CLI requirement.
This check establishes unsigned package continuity, not a signed, self-contained application or a released version.

## Source and policy continuity

The local integration preserves public PR [331](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/331)
head `d061dd46fd17f4bc2f74cae97cdec658daadf007` and its existing preparation merge
`b7ae12d963185470fe25e59e292f915165c890a3`. It merges actual main
`e822215e52dfeffc31bd884c8c08f4ca833a2712`, containing PR 500's repaired controller and build preparation.
There were no merge conflicts. Electron runtime, packaging scripts, tests and README remain byte-identical to the
public PR head. All integration changes come from main; no desktop dependency or lifecycle deadline changes.

The main branch's Apple-silicon-only macOS CI policy, shared architecture guard and native architecture check remain
unchanged. Its 500-session listing performance test retains the owner's one-second target. Linux coverage and the
remaining test limits remain as declared on main. Refresh again from actual main before publication if it advances.

## Reproduced packaging checks

The following commands ran in the isolated checkout with Node.js 24.19.0 from the locked Python development group
first on PATH and pnpm 12.15.0 from the repository tool installation:

```sh
make setup
make desktop-electron-test
make desktop-electron-package
make ci-macos-arm
```

All 99 Electron tests passed, including the real main-process quit handler under isolated process/Electron fixtures
for SIGTERM, SIGINT and SIGHUP, clean and unclean child exits, manual quit, closed package inventory and Node policy.
The package verifier checked every approved ASAR entry, exact source bytes, generated version metadata and hashes.
The unsigned darwin-arm64 artifact carries core version 0.9.14 from this checkout's metadata. Its ASAR SHA-256 is
`7fddd4a1a45e6ba06ca6906c59c95f99d25edfe1d62757b4041ebc9bd60a2510`.

An actual packaging invocation with `CI=true` and local Node.js 26.12.1 failed at the strict version guard.
The existing ASAR's digest remained unchanged. This exercises refusal before package output mutation, in addition
to the Node policy unit tests. It does not establish deterministic bundle bytes across different machines.

`pnpm --dir desktop test:native` also passed: real Electron 45 rendered an isolated development fixture and made
its authenticated lifecycle request. The service was a temporary test endpoint, not an installed core service.
This did not launch the packaged artifact or establish production CLI/native acceptance.

`pnpm --dir desktop test:navigation` passed the real Electron page-navigation, same-origin redirect and initial
authenticated-redirect cases against isolated servers; cross-origin destinations received no request.
The focused macOS architecture and session-store suites passed all 15 tests, including the one-second listing case.
The documentation source check passed with no errors; its existing warnings remain.

## Remaining release evidence

The integrated head needs its own ordinary hosted CI after release orchestration permits another run. Earlier
Linux and macOS Electron jobs passed on the public PR head; its aggregate CI failed in macOS CLI end-to-end tests.
Those historical results do not validate this integration. No hosted run, public PR update or publication was
requested during this preparation.

Native acceptance must use the exact intended release artifacts and verify the authenticated installed CLI journey,
window reload, graceful owned-child shutdown, unexpected child exit and listener cleanup in disposable profiles.
A packaged app's local profile isolation remains unverified, so its native harness is restricted to explicitly
disposable CI. The development harness uses temporary profiles and does not prove a packaged installation.

The shell still requires an installed `hz` on PATH. Signing, notarization, an installer/uninstaller, automatic update,
Python provisioning and background service operation are not established by these checks. Publication and native
acceptance follow the preceding core release; this preparation does not authorize either operation.
