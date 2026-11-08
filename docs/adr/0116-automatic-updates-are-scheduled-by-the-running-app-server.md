---
amended-by: 0117, 0137
status: proposed
date: 2026-11-02
implementation: built
decision-makers: maintainer
related: 0006, 0031, 0047, 0083, 0085, 0114
implemented-by: 452
---

# 0116: Automatic updates are scheduled by the running app server

## Context and Problem Statement

Updating today needs a person: the CLI notice (ADR 0031) and the footer's update pane (ADR 0085) say a release
exists, and someone clicks Update. The owner asked for updates that happen by themselves, on by default, with a short
warning and a way to put them off, and never while someone develops hz. Who decides when to update, where does that state live, and what happens when nobody is
watching or something is running?

## Decision Drivers

- On by default, also for existing installations after they upgrade; stable releases only.
- Never while hz is being developed or tested: an update would replace code under work or race a test.
- Two windows, a reload and no window at all must all see the same plan.
- Never cut off a chat turn or a running helper job.
- A failed update must leave the old version installed and running, say so once, and not retry in a loop.

## Considered Options

- **The `hz ui` server owns the schedule and applies with the existing installer and restart** (chosen)
- **The browser owns the schedule**: each window would keep its own countdown, a reload would lose it, and nothing
  would update with no window open.
- **The launchd daemon tick applies updates**: it can't restart the app server in place on the same port, and it
  ticks only every few minutes, too coarse for a 30-second countdown.

## Decision Outcome

Chosen option: **the `hz ui` server owns the schedule**, because it already serves every window, can restart itself in
place (ADR 0047, 0083) and runs whether or not a window is open.

- The kernel setting `auto_update` ("Automatic updates", default **on**, user-level only) turns it off and on. Two
  switches edit it, both live (no restart): one in the footer's Updates pane and the setting's row in Settings →
  Updates, found there by search as well; each says in one line what it does, and a change in one shows in the other.
  `hz system config set hz.auto_update false` and `HZ_AUTO_UPDATE=false` work too. It runs only on the stable channel,
  on a restartable `hz ui`, on an installation the web update supports, and never while `update_check` is off: then
  nothing is asked at all, and both switches are disabled and say that update checks are off.
- **Development is judged by facts, not a setting.** The server never checks, schedules or applies, and shows no
  popup, when any of these holds at start: the `heinzel` package is imported from outside `site-packages` (a source
  tree) or its distribution is an editable install; the environment's folder sits in a checkout (a `.git` or
  `pyproject.toml` next to the `.venv`); `HZ_DEV`, `HZ_TEST`, `CI`, `PYTEST_CURRENT_TEST`, `PYTEST_XDIST_WORKER`,
  `TEST_WORKER_INDEX` or `PLAYWRIGHT_TEST_BASE_URL` is set; make (`MAKELEVEL`) or an npm script such as the Vite dev
  server (`npm_lifecycle_event`) started it; or the state folder isn't the user's default. Both switches are then
  disabled and say "Updates are managed by your source checkout." (or, for the other facts, that hz runs for
  development or tests). An installed wheel or uv tool install with the default state folder keeps the default on.
  `HEINZEL_AUTO_UPDATE_TEST=1` counts none of the facts; only the end-to-end test of the real flow sets it.
- The server asks for a newer stable hz release (GitHub) and newer releases of installed plugins (PyPI) every 30
  minutes, with up to 5 minutes of random jitter (the first check 1 to 5 minutes after start). hz and the plugins are
  asked apart: a GitHub or PyPI error about one keeps the other's news. GitHub is asked about hz's tag only for an
  installation from a release tag, at announce time and again right before installing; the privacy page lists every
  request.
- **Only old enough, stable, unyanked releases are announced.** A release's age is the last upload of any of its
  files to PyPI (a file added to an older release later makes it that new). hz counts once its release on PyPI is
  `auto_update_min_age_hours` old (default 6), a plugin once `auto_update_plugin_min_age_hours` old (default 24), so
  a broken or hijacked release can be withdrawn first; a release missing from PyPI or of unknown age counts as too
  new. Pre-releases, GitHub drafts and yanked releases never count. The commit that hz's tag names is recorded when
  hz is announced. `hz system update` on the stable channel never moves onto a
  pre-release either, not even from one (this amends ADR 0006).
- **hz installs itself only once its release is accepted.** The release train judges every published release with
  an acceptance run and uploads the verdict as the release asset `acceptance.json` (`project`, `version`,
  `accepted`). A newer hz old enough to install but without that asset, or with one that does not say accepted for
  exactly this version, is held: the Updates pane says so (`core_held`) and `web.auto_update.acceptance` logs the
  check with its outcome. A verdict that can't be read counts as a failed check. Plugins still move. `hz system
  update` never asks, so a person can always install the release by hand.
- When something is newer and nothing is busy (no request that changes state is in flight, no daemon job holds the
  tick lock), it opens a 30-second countdown. A request is in flight until its response body has been sent or
  dropped, so a streamed chat turn holds it off while it streams, and a streamed operation holds it in its own thread
  while it runs on after the browser left. `GET /api/auto-update` tells every window the plan and the seconds
  left; `POST /api/auto-update/postpone` moves the apply time 1 hour later, as often as wanted. Work that starts
  during the countdown cancels it until things are idle again.
- At the apply time the tag hz installs from is resolved again; if it names another commit than at announce time,
  nothing is installed. Then the private operation `system.apply-announced` (no CLI, MCP or REST head offers it)
  installs **exactly the announced versions** and nothing else:
  - It copies the uv tool folder (hz, every plugin, their dependencies and the receipt, not the continuity record)
    under the state folder, which must be on the same disk; the copy counts only once it is complete and renamed
    into place. Without a copy nothing is installed.
  - It runs `uv tool install` with the receipt's own requirements and, as transient constraints, every distribution
    in the tool folder at its installed version (dependencies too), except `heinzel==X` and each announced
    `plugin==Y`. uv installs exactly these or fails before it changes anything. Only the announced packages are asked
    again and reinstalled (`--refresh-package`, `--reinstall-package`, never `--refresh` or `--reinstall`); a dependency the new versions need that isn't installed yet
    resolves only among uploads older than the plugins' minimum age (`--exclude-newer`, and hz's own age for hz).
    The constraints, and the resolver options uv records for this run in the receipt's `[tool.options]`
    (`exclude-newer`, `exclude-newer-package`), are taken out of the receipt again afterwards, exactly as added; the
    rest of the receipt stays as it was. An integration test runs this against real uv, offline, in temporary tool
    folders.
  - hz from a release tag is installed from the commit the tag named when it was announced, never the tag, and the
    receipt names the tag again afterwards, so later updates go on following release tags. Without a recorded
    commit (also for a plugins-only update, the installed commit), the update is made by hand.
  - **Plugins from git, a folder or a URL are never updated, refreshed or reinstalled automatically** (except
    those pinned to a tagged GitHub commit, ADR 0137), but they don't hold the rest: hz and the plugins from PyPI
    update around them. Each goes into uv exactly as installed: one
    from git at its installed commit (the receipt follows its branch or tag again afterwards), held at its version,
    never named for refresh or reinstall, so what is installed satisfies it and nothing is built. Afterwards its
    version, the hashes of all its files (`RECORD`) and its source (`direct_url.json`) must be exactly as before, or
    everything is put back. If a new hz doesn't fit the range such a plugin declares for `heinzel`, nothing is
    installed and one line says to update the plugin first. The Updates pane names them: "Update by hand: …".
  - It verifies by reading the new environment's metadata, never by running it: exactly these versions, and hz from
    git at the recorded commit.
  - It starts the newly installed hz as a trial: `hz ui --port 0` with its own token, on a throwaway copy of the
    configuration and empty state, data, cache and log folders, with update checks off, deleted afterwards, so the
    live folders are never touched. It waits up to 90 seconds for `/api/lifecycle` to answer with the new version.
  - Only then is the change recorded (continuity, ADR 0108), the daemon handed over, and the server restarts in
    place. If the restart itself fails, the new version stays installed, the old one keeps running, and every window
    says to restart hz ui to finish. Windows reconnect without signing in and reload once the version changed, but
    not while a composer has unsent text or a turn runs: the window says "Reload to finish the update" and reloads
    by itself once they are idle.
- **Rollback.** If uv fails, the metadata or the commit differ, or the trial start fails, nothing restarts. The tool
  folder itself is never replaced: first everything the update left moves aside, then each entry of the copy moves
  back into it, each an atomic rename. hz, every plugin and the receipt are byte for byte as they were, so the
  installation's continuity fingerprint (which binds the folder and the content in it) is the recorded one again,
  the standard app starts, and the daemon mutation completes with a handover to the same code, none left pending.
  One plain message says what happened. An update whose trial failed is never tried again; a newer one is. Other
  failures try again no sooner than 6 hours later. **A copy left on disk is the truth:** if the process died after
  taking it, during the install, or half way through putting it back, the next `hz ui` start puts it back
  (`system.recover-announced`, only while no other server holds the apply lock), as does the next apply. If a crash
  leaves hz unable to start at all, `uv tool install --reinstall heinzel` repairs it.
- The plan (what is pending, the apply time, postponements, the last failure and its message, a refused update)
  lives in the server and is saved under the state folder, so it survives a restart.
- **Two servers on one state folder.** Every change to the plan reads the state file, changes it and writes it back
  under an exclusive `flock` (`auto-update.lock`), so one server's postpone and another's dismissal both stay; no
  last writer wins. Applying takes a second lock (`auto-update-apply.lock`) held until the process is replaced; a
  server that can't take it leaves the update to the other and looks again an hour later.

### Consequences

- Good: one owner of the plan; the browser only shows it.
- Good: the installer's own guards (lifecycle scope, transient constraints, metadata checks, continuity) do the work;
  the automatic path only adds exact versions, the copy and the trial start.
- Bad: nothing updates while `hz ui` is not running; the launchd daemon alone does not update.
- Good: on by default, yet a developer's checkout, a test run or CI never updates itself.
- Bad: a version that passes the trial start but fails later in the restart itself (the trial ran the same command)
  has no automatic way back; the old server has already handed over. The trial makes this unlikely.
- Bad: the copy of the environment costs disk space and a few seconds per update.
- Bad: PyPI's file hashes are not passed to uv as `--require-hashes`: a uv tool install resolves dependencies anew
  and uv takes required hashes only for every requirement of a fully pinned set. uv still checks the hashes the
  index serves for each file it downloads; the minimum age and the exact versions are the defence here.
- Bad: a git-tag installation still runs the build backend of the commit it installs, the one recorded at announce
  time.
- Bad: a plugin from a folder whose source changed since it was installed can make uv rebuild it; the file check
  then puts everything back, and that update is made by hand.
- Bad: an update whose new version needs a newer dependency than the installed one can't apply automatically; it
  fails before installing, says so, and is made by hand.
- Bad: a second server on the same state folder that didn't apply keeps running the old version until it restarts.

### Confirmation

Unit tests of the scheduler fail when a countdown opens while busy, a postpone does not move the apply time by an
hour, a failure retries sooner than 6 hours, a failed trial start restarts, a put-back replaces the tool folder or
leaves the continuity record unmatched or a handover pending, an interrupted put-back isn't finished, uv is asked for
anything but exact versions for every installed distribution, `--refresh` or a missing `--exclude-newer` is passed,
a plugin from git, a folder or a URL is refreshed, reinstalled or changed by an update or a put-back, or holds the
rest, a new hz outside such a plugin's declared range installs anything, a tag rather than its recorded commit is installed, a copy left
by a killed process isn't put back, a partial copy is, an installed hz runs before it is verified, a moved tag
installs anything, a release's age isn't its last upload to PyPI,
the trial start writes to a live folder, the default is off, update checks off still checks, a release younger than
its minimum age, a pre-release or one of unknown age is announced, other versions than announced or a moved tag are
kept, two servers lose each other's changes or both apply, any one development fact leaves updates on, or a faked
installed run counts as development. An integration test fails when a streamed turn stops counting as work before
its stream or its operation has ended. Vitest
covers both switches, live save and the disabled state with its line. An end-to-end test with a fake newer release,
run with `HEINZEL_AUTO_UPDATE_TEST=1`, checks in a real browser that both switches show each other's change, then the
countdown, postpone, trial start and apply.

## More Information

Implementing pull requests:

| Pull request | What it builds |
| --- | --- |
| #452 | setting (on by default), switches, development facts, scheduler, minimum age, exact versions, trial start, environment copy and rollback, locks, endpoints, popup, tests |
