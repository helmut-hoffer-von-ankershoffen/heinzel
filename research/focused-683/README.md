# Focused admission input for Core PR #683

Astra reviewed the exact locator repair `e79108052f6d4071d1b0bb802c758d47c5aa3a33` on provisional base `b234067b172f33db59f98c9ea38808b3d5ab9bc5`. Its parent is the earlier composition `8558755d5144280e92fe4975875dd7aee3807f51`. The repair rejects embedded PATH separators, checks ownership and mode through each added directory and symlink hop, rejects absent or inaccessible directories, and uses the system default if no PATH entries remain. Explicit PATH entries keep their order. The 15-path candidate diff includes five Python production files and one TypeScript production file.

The doctor check, standard installer, updater and UI service all use the same locator. On the Mini, an isolated probe of the exact source set PATH to `/usr/bin:/bin:/usr/sbin:/sbin` and used an empty fixture HOME. Plain lookup found no uv. The doctor reported UP, and all four consumers selected the actual `/opt/homebrew/bin/uv`. The locator tests exercise the standalone user install, configured locations and explicit precedence. A fixture treats `/usr/local/bin` as trusted to check Intel Homebrew path selection. Actual Intel hardware remains unverified. The five local macOS tests passed. The hosted macOS evidence lane independently exercises the two fake-uv installer cases and three guarded service cases on the repinned candidate.

The historical Python selection and nine new locator cases gave 525 passes and 76.86% aggregate locally. Add the existing uninstall, service-removal, UI refresh, announced update, tool policy and installation continuity suites, which own paths through the two under-covered modules. The revised 679-case selection passed on CPython 3.14.8 on the Mini.

| Affected Python module | Covered statements and branches | Local coverage |
| --- | ---: | ---: |
| `heads/install.py` | 372/454 | 81.94% |
| `kernel/_installation.py` | 883/1024 | 86.23% |
| `kernel/_uv_locate.py` | 96/98 | 97.96% |
| `modules/system/doctor/__init__.py` | 83/90 | 92.22% |
| `modules/ui/_service.py` | 675/836 | 80.74% |
| All five | 2109/2502 | 84.29% |

The nine focused Vitest assertions passed with 60/64 source lines and 175/193 combined statements and branches on `web/src/updates/updateState.ts`. The locked project build group provides Node and the separate frozen measurement lock supplies the matching Vitest coverage provider. Local gitleaks scanned 594 commits and found no leaks. License, Python vulnerability and web vulnerability audits passed. Hosted Linux measurement, final-base source review and exact PR Sonar remain required. Sonar readback still names the current PR head `c25199bc0`.

- Mac ARM [42777749675](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/runs/42777749675) passed five fake-uv and guarded service cases on the earlier `8558755d` composition.
- Linux [42746011689](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/runs/42746011689) passed two original factory regressions on CPython 3.15.2 at `f73cb104`.
- Mac ARM [42721507069](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/runs/42721507069) passed one offline desktop-owned CLI fixture at `1cb1be77`. Installed update and signed Finder acceptance remain open.

The three historical archives were downloaded read-only and verified against GitHub's artifact SHA-256 and exact passed JUnit cases. They remain diagnostic history. The isolated Python plan excludes the opt-in test for an installed receipt. The standard Ubuntu selection omits the three Darwin service cases, which run on macOS. The separate Linux CPython 3.15.2 lane runs the two original factory regressions.

The workflow draft stays outside `.github/workflows` and the manifest holds execution. The read-only bootstrap runs real lint, type, quarantine, secret, dependency and Sonar checks. Sonar judges analysis of the actual open PR at the exact candidate SHA and current main, with both quality gates green and reliability, security and maintainability rated A. All five Python files individually and in aggregate must reach an unrounded 80% of statements and branch opportunities. TypeScript must reach 80% on both source lines and combined statements and branches. The evidence includes raw coverage, test results and lane identity.

After #650 merges, repin the protected base, candidate, tree, source review and owning collections. Independently review the exact new composition and bootstrap. Verify the live ruleset, actual PR head and ordered test-merge parents and tree, and original PR ancestry around any separately reviewed ref update. Run the read-only `python3 -I research/focused-683/verify.py <run-id> <reviewed-helper-sha>` before the separately reviewed status writer. It adapts #650’s exact-attempt archive checks and recalculates the thresholds from raw coverage. Require the exact first attempt, all seven named jobs green, exact bootstrap SHA, successful upload-step log bindings to artifact ID and size, archive digests and full inventory, matching lane identity and cases, and independently recalculated coverage. Require actual structural, security and Sonar success on that attempt. Keep `CI OK`, `coverage-floor`, `merge-gate` and root-owned writer serialization. Full final release-candidate CI retains its whole-project 80% floor and existing 90% `make test` threshold.
