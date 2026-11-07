# Update lifecycle: every component and head after an update

Audit of 2026-11-07. Question: after a successful update, does every installed component end up running the new
code, how is that verified, and where is it missing? Trigger: a supervised web server kept serving 0.9.22 after
`hz system update --channel stable` installed 0.9.26 and handed over the daemon; the web footer showed the old version
until a manual `hz ui restart`.

## Update entry points

| Entry point | Code | Who runs it | Mutation boundary |
| --- | --- | --- | --- |
| `hz system update` (CLI) | `modules/system/_update.py` `UpdateService.update` | the person's shell, old code | `DaemonInstallationLifecycle.mutation` (marker `daemon/mutation-pending.json`) |
| Footer "Update" (PWA) | same operation over REST, then `restartAndApply` in `web/src/updates/controller.ts` | the web server, old code | same |
| Automatic update | `heads/web/_auto_update.py` `AutoUpdater.apply` → `system.apply-announced` → `UpdateService.apply_announced` | the web server, old code | same, plus a tool-folder snapshot (`ToolSnapshot`) |
| Interrupted automatic update | `system.recover-announced` at web start | the web server | snapshot restore |
| Managed app install/upgrade | `heads/install.py` `_managed_install` | the installer | `DaemonInstallationLifecycle.quiescent` |
| Plugin add/remove | `modules/system/plugin/_service.py` | CLI or web | plugin `_mutation` |

## Heads and components after an update

| Component / head | Long-running? | How it moves to the new code | Verification of the live version | State before this change | Now |
| --- | --- | --- | --- | --- | --- |
| Core tool folder | n/a | `uv tool install` in one resolver run with every plugin | metadata inspection (`verify_installed_requirements`), `hz --version` | verified | unchanged |
| Plugins (index) | n/a | same run; a blocker is kept, then dropped | metadata versions per plugin | verified | unchanged; dependency closure see below |
| Plugins (git, folder, URL) | n/a | updated by hand; automatic updates hold them | identity unchanged (`installed_identity`) | verified | unchanged |
| Daemon (`launchd`/`systemd`) | yes | handover: `launchctl kickstart` / `systemctl try-restart` after the mutation completes | the handed-over tick writes `handover-observation.json` with the version it imported | requested only; "a fresh service tick is pending" | the update waits up to 90 s for that observation and reports `verified`, `failed` (other version) or `pending` with `hz system daemon status` |
| Supervised web server (`hz ui serve --supervised`) | yes | owned restart through its private control socket (`hz ui restart`) | `hz ui status`: a different ready instance and the version it loaded | **missing** for the CLI path: it kept the old code until someone restarted it | the update restarts it after the mutation completes (even on an unchanged core, since plugins may have changed) and waits up to 12 s for a new instance reporting the target; `failed`/`pending` name `hz ui restart` |
| Web server that runs the update (footer, automatic) | yes | restarts itself in place after the operation returns | at its next start: `restarting_to == version` → success, else failure | verified for itself | reported as `self`; never restarted from inside |
| Foreground `hz ui serve` (unsupervised) | yes | no owned restart path; the person restarts it | none | not covered | not covered: it has no service record; documented here |
| Native notifier (`Heinzel Notifier.app`) | no, one process per notice | `ensure()` copies the bundled app when its digest differs, at the next notice from the new daemon | tree digest of the copy against the bundle | converges lazily | unchanged: there is no running process to restart |
| Desktop app (Electron shell) | yes, while open | the new web server's update check calls `install_desktop(update_only=True)`; deferred while the app is in use | archive digest and bundle checks in `_desktop_installation.py` | converges at the first check after restart (1-5 min), only with automatic updates on | reported as `pending` in the update receipt; still not verified by the update itself |
| PWA in the browser | yes | `/frontend` identity changes; the page reloads after `restartAndApply` | frontend identity | verified on the footer path | unchanged |
| Auto-update state shown in the PWA | n/a | `web/auto-update.json` | `AutoUpdateStatus` | **stale**: a failure ("The automatic update didn't work…") stayed after a later successful update by other means | at web start, news no newer than the running version is dropped, and a failure reported while another version ran (or from an older state file whose news is now installed) is cleared |

## Partial failure and rollback

| Situation | Behaviour |
| --- | --- |
| uv fails before installing | mutation aborted unchanged; nothing restarted |
| Automatic update installs but the new hz doesn't start | snapshot restored byte for byte; daemon handed over to the same code; message says it won't retry that release |
| Announced plugin needs a dependency that isn't announced (plugin depending on plugin) | uv can't install exactly the announced versions; snapshot restored; no head restarted (integration test `update_heads_real_uv_test.py`) |
| A head can't be verified after a successful install | the installation stays updated (it is consistent); the receipt says which head is `pending`/`failed` and the command that finishes it; the message never claims the update complete |
| Process dies mid-mutation | `mutation-pending.json` blocks web launch and supported commands until recovery; dead-owner recovery is handled separately (branch `fix/reboot-stale-fence`) |

## Plugin → plugin dependencies (gaps found, fixed separately)

- `hz system update` steps a blocking plugin down to `drop`. If another kept plugin requires it, uv installs it anyway
  as a dependency, the post-install metadata check refuses the result ("forbidden" distribution present) and the
  mutation ends uncertain. The dependent must be dropped with it.
- `hz system plugin remove X` while another installed plugin requires `X`: uv keeps `X` as a dependency and the
  removal fails only after the package change. It must be refused up front, naming the dependents.

## Verification receipt

`UpdateResult.heads` and `AnnouncedUpdate.heads` list one `HeadRefresh` per installed head (`daemon`, `web`,
`desktop`): `verified` only from the live process's own report of the target version.
