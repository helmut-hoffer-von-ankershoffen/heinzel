"""Acceptance scenario, slice 1 (advisory): the previous release updates itself to release N. Stdlib only.

One story on one private backend, in one private home on a hosted runner:

- P0: PyPI latest is N; the app profiles of N and of the release before it are bound by sha256 to PyPI's wheels.
- B1: the previous release is installed as published (`uvx heinzel@N-1` with its app profile).
- B2: user state: an extra plugin, a disabled default, a setting, the local test model selected for chat.
- B3: two `hz ui serve` processes on the same state folder (the fixture of the 2026-11-21 bug).
- B4: the automatic updater's own scheduled check, no button and no update command: both servers serve N.
- B5: the state after the update: configuration identical, plugin choices kept, installed Core bytes are N's wheel.
- T1: one web chat message, answered by the local test model (`dummy`, always 42).
- T16: no forbidden error in any log; every step is PASS, FAIL or UNPROVABLE in scenario.json.

It reuses the hash-pinned harness (`run_acceptance.py`) for private homes, owned processes and every byte check, and
publishes nothing. Its verdict is advisory: no release waits for it yet (SCENARIO.md, slice 1).
"""

import argparse
import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

DIRECTORY = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("acceptance_harness", DIRECTORY / "run_acceptance.py")
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)

SCENARIO = "B"
HARNESS.CASES[SCENARIO] = "scenario_upgrade"  # this process's copy only: the harness's own cases are unchanged
STEPS = ("P0", "B1", "B2", "B3", "B4", "B5", "T1", "T16")
UPDATE_DEADLINE = 900  # first check 1 to 5 min after start, a 30 s countdown, the apply and both restarts
POLL_SECONDS = 5
ANSWER = "42"
CHAT_MESSAGE = "What is the answer?"
# Settings a user can set; the scenario records them. The age gates hold a release of minutes off for hours.
USER_SETTINGS = {
    "clock.zones": '["Europe/Berlin","Asia/Tokyo"]',
    "capabilities.ai_access": "dummy",
    "hz.auto_update_min_age_hours": "0",
    "hz.auto_update_plugin_min_age_hours": "0",
}


def previous_release(version, releases):
    """The newest final, unyanked release before `version` in PyPI's release map."""
    key = HARNESS.release_key
    older = [
        name
        for name, files in releases.items()
        if HARNESS.is_final_version(name) and key(name) < key(version) and files and not all(f.get("yanked") for f in files)
    ]
    HARNESS.check(older, f"No published release precedes {version}")
    return max(older, key=key)


def bind_releases(args, parent):
    """P0: PyPI latest is N, and both app profiles are bound to PyPI's wheels by sha256. Executes nothing."""
    pypi = json.loads(HARNESS.fetch("https://pypi.org/pypi/heinzel/json", 8 * 1024 * 1024))
    latest = pypi["info"]["version"]
    if args.expect_version == "published":
        args.expect_version = latest
    HARNESS.check(latest == args.expect_version, f"PyPI latest is {latest}, expected {args.expect_version}")
    args.from_version = args.from_version or previous_release(args.expect_version, pypi["releases"])
    HARNESS.check(
        HARNESS.release_key(args.from_version) < HARNESS.release_key(args.expect_version),
        "The starting release must precede the target",
    )
    old = HARNESS.bind_old_app(args.from_version, parent / "accepted-from")
    new = HARNESS.bind_old_app(args.expect_version, parent / "accepted")
    core = next(a for a in new["profile"]["artifacts"] if a["name"] == "heinzel")
    if args.expect_wheel_sha256:
        HARNESS.check(core["sha256"] == args.expect_wheel_sha256, "N's wheel differs from the expected sha256")
    return old, new, {
        "pypi_latest": latest,
        "from_version": old["version"],
        "from_profile_sha256": old["profile_sha256"],
        "target_version": new["version"],
        "target_profile_sha256": new["profile_sha256"],
        "target_core_wheel_sha256": core["sha256"],
    }


def post(session, path, body):
    """An authenticated JSON POST to one owned server; the bearer token never leaves the loopback."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), HARNESS._NoRedirect())
    request = urllib.request.Request(
        session.base + path,
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + session.token, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener.open(request, timeout=120) as response:
            return response.status, json.loads(response.read(8 * 1024 * 1024) or b"null")
    except urllib.error.HTTPError as error:
        text = error.read(2048).decode(errors="replace")
        error.close()
        return error.code, text


def get_json(session, path):
    status, body = session.http(path)
    HARNESS.check(status == 200, f"{session.label}: GET {path} returned {status}")
    return json.loads(body)


def settled(status, version):
    """The plan of a server that runs `version` and has nothing left to do: no news, no failure, no apply."""
    return (
        status.get("version") == version
        and status.get("phase") == "idle"
        and not status.get("core")
        and status.get("core_held") is None
    )


def default_state_folder(case):
    """The state folder a user has: `platformdirs.user_state_path("hz")` under the case's private home.

    The updater refuses any other state folder as development (ADR 0116), so the scenario must not set HZ_STATE_DIR.
    The harness's checks keep reading `state_dir`, a link to that folder.
    """
    if sys.platform == "darwin":
        folder = case.root / "home" / "Library" / "Application Support" / "hz"
    else:
        folder = Path(case.env["XDG_STATE_HOME"]) / "hz"
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    link = case.root / "state_dir"
    HARNESS.check(not os.path.lexists(link), "The private state link already exists")
    link.symlink_to(folder, target_is_directory=True)
    case.env.pop("HZ_STATE_DIR", None)
    return {"state_folder": str(folder)}


def prepare_state(case):
    """B2: what a user has before the update; the configuration snapshot is compared after it."""
    HARNESS.hz(case, "system", "plugin", "add", "heinzel-example", "--confirm", label="user-plugin-add")
    HARNESS.hz(case, "system", "plugin", "disable", "heinzel-resource", label="user-disable")
    for key, value in USER_SETTINGS.items():
        HARNESS.hz(case, "system", "config", "set", key, value, label=f"user-config-{key}")
    HARNESS.expect_plugins(case, heinzel_example={"enabled": True}, heinzel_resource={"enabled": False})
    for entry in case.plugins_expected.values():
        entry["version"] = None  # the profile's versions are recorded, not compared, once a plugin was added
    case.notes["python_before"] = HARNESS.installed_interpreter(case)
    case.notes["config_before"] = HARNESS.snapshot_files(case.root / "config_home")
    HARNESS.check(case.notes["config_before"], "No user configuration exists to retain")
    return {"user_plugin": "heinzel-example", "disabled_default": "heinzel-resource", "settings": USER_SETTINGS}


def start_servers(case, version):
    """B3: two `hz ui serve` on one state folder, both answering as the previous release."""
    case.env.pop("HZ_UPDATE_CHECK", None)  # the harness turns checks off; the updater needs them on, as a user has
    command = [str(case.bin / "hz"), *HARNESS.foreground_ui_command(version)]
    servers = []
    for name in ("server-a", "server-b"):
        servers.append(case.foreground(command, name))
    evidence = {}
    for server in servers:
        info = get_json(server, "/api/system/info")
        HARNESS.check(info["version"] == version, f"{server.label} runs {info['version']}, expected {version}")
        status = get_json(server, "/api/auto-update")
        HARNESS.check(status.get("enabled") is True, f"{server.label}: automatic updates are off: {status.get('reason')}")
        evidence[server.label] = {"version": info["version"], "pid": server.process.process.pid, "port": server.port}
    HARNESS.check(len({e["port"] for e in evidence.values()}) == 2, "Both servers must listen on their own port")
    return servers, evidence


def await_update(case, servers, target, deadline):
    """B4: wait for the scheduled check, the countdown, the apply and both restarts; never ask for a check."""
    started = time.monotonic()
    seen = {server.label: [] for server in servers}
    last = {}
    while time.monotonic() - started < deadline:
        for server in servers:
            HARNESS.check(server.process.status is None, f"{server.label} exited during the update")
            try:
                status = get_json(server, "/api/auto-update")
            except (HARNESS.StepFailed, OSError, ValueError):
                continue  # a restart in place keeps the socket; a request that meets it is asked again
            last[server.label] = status
            phase = status.get("phase")
            if not seen[server.label] or seen[server.label][-1] != phase:
                seen[server.label].append(phase)
        if len(last) == len(servers) and all(settled(s, target) for s in last.values()):
            break
        time.sleep(POLL_SECONDS)
    phases = {label: " > ".join(p) for label, p in seen.items()}
    HARNESS.check(
        len(last) == len(servers) and all(settled(s, target) for s in last.values()),
        f"Not both servers settled on {target} within {deadline}s: {phases}; last "
        + json.dumps({k: {f: v.get(f) for f in ("version", "phase", "core", "core_held", "message")} for k, v in last.items()}),
    )
    messages = {label: status.get("message") or "" for label, status in last.items()}
    HARNESS.check(
        any("updated automatically" in message for message in messages.values()),
        f"No server reports an automatic update: {messages}",
    )
    HARNESS.check(not any("didn't work" in message for message in messages.values()), f"A failure remains: {messages}")
    for server in servers:
        info = get_json(server, "/api/system/info")
        HARNESS.check(info["version"] == target, f"{server.label} serves {info['version']}, expected {target}")
    return {"phases": phases, "messages": messages, "seconds": round(time.monotonic() - started, 1)}


def verify_after(case, old, new):
    """B5: Core bytes are N's wheel, configuration and plugin choices are kept, the CLI reports N."""
    core = next(a for a in new["profile"]["artifacts"] if a["name"] == "heinzel")
    wheel = new["accepted"] / core["filename"]
    case.ctx["from_app"], case.ctx["update_target"] = old, {"mode": "published", "version": new["version"], "wheel": wheel}
    case.expect_version, case.core_wheel_override = new["version"], wheel
    profile = HARNESS.post_update_profile(case)
    files = HARNESS.verify_files(
        case, profile=profile, accepted=new["accepted"], receipt_profile=old["profile"], plugins=False
    )
    HARNESS.check(
        HARNESS.snapshot_files(case.root / "config_home") == case.notes["config_before"], "Configuration changed"
    )
    evidence = {"core_files": files["wheel_files_identical"], **HARNESS.verify_state(case, "after")}
    evidence["interpreter"] = HARNESS.verify_update_interpreter(case, migrated=False)
    evidence["plugin_versions_after"] = {
        p: v["version"] for p, v in HARNESS.plugin_map(case, "versions-after").items()
    }
    return evidence


def chat(session):
    """T1: one web chat message; the answer is the local test model's fixed one."""
    status, created = post(session, "/api/heinzel/new-chat", {"request_id": "scenario-new"})
    HARNESS.check(status == 200, f"new-chat returned {status}: {str(created)[:300]}")
    body = {"session_id": created["session_id"], "request_id": "scenario-1"}
    status, accepted = post(session, "/api/heinzel/chat", {**body, "message": CHAT_MESSAGE})
    HARNESS.check(status == 200, f"chat returned {status}: {str(accepted)[:300]}")
    status, answer = post(session, "/api/heinzel/answer-chat", body)
    HARNESS.check(status == 200, f"answer-chat returned {status}: {str(answer)[:300]}")
    text = (answer.get("message") or {}).get("text")
    HARNESS.check(text == ANSWER, f"The chat answered {text!r}, expected {ANSWER!r}")
    return {"answer": text, "model": "dummy-42"}


def stop_all(case, servers):
    """Stop every owned server and prove cleanup; the first failure is kept, the rest are still stopped."""
    errors = []
    for server in servers:
        try:
            server.stop()
        except HARNESS.StepFailed as error:
            errors.append(str(error))
    HARNESS.check(not errors, "; ".join(errors))
    return {"stopped": [server.label for server in servers]}


def record(result, ident, status, case=None, why=None):
    result["steps"][ident] = {"status": status, **({"why": why} if why else {})}
    if case is not None:
        result["steps"][ident]["evidence"] = case.steps[-1].get("evidence") if case.steps else None


def run_scenario(args, parent, result, ctx_base):
    """Every step in order; the first failure stops the story, and every later step says it did not run."""
    old, new, identity = bind_releases(args, parent)
    result["release"] = identity
    record(result, "P0", "PASS")
    ctx = {
        **ctx_base,
        "args": SimpleNamespace(expect_version=old["version"]),
        "profile": old["profile"],
        "accepted": old["accepted"],
        "explicit_profile": None,
    }
    case = HARNESS.Case(SCENARIO, parent, ctx)
    result["root"] = str(case.root)
    servers = []
    current = "B1"
    try:
        case.provide_uv()
        case.step("the user's default state folder, inside the private home", lambda: default_state_folder(case))
        case.env["HZ_APP_PROFILE"] = old["url"]
        try:
            HARNESS.app_cycle(case, f"published {old['version']} install", ["uvx", f"heinzel@{old['version']}"])
        finally:
            case.env.pop("HZ_APP_PROFILE", None)
        record(result, "B1", "PASS", case)
        current = "B2"
        case.step("user state before the update", lambda: prepare_state(case))
        case.step("state before the update verified", lambda: HARNESS.verify_state(case, "before"))
        record(result, "B2", "PASS", case)
        current = "B3"
        servers, evidence = case.step("two servers on one state folder", lambda: start_servers(case, old["version"]))
        case.steps[-1]["evidence"] = evidence
        record(result, "B3", "PASS", case)
        current = "B4"
        target = new["version"]
        case.step(
            "the automatic update, no button", lambda: await_update(case, servers, target, args.update_deadline)
        )
        record(result, "B4", "PASS", case)
        current = "T1"
        case.step("web chat answered by the local test model", lambda: chat(servers[0]))
        record(result, "T1", "PASS", case)
        current = "B5"
        case.step("stop both servers", lambda: stop_all(case, servers))
        case.step("state after the update", lambda: verify_after(case, old, new))
        record(result, "B5", "PASS", case)
        current = None
    except (HARNESS.StepFailed, HARNESS.Refused, HARNESS.Unprovable) as error:
        status = {HARNESS.Refused: "REFUSED", HARNESS.Unprovable: "UNPROVABLE"}.get(type(error), "FAIL")
        record(result, current, status, why=HARNESS.redact(str(error))[:800])
    finally:
        for server in servers:
            if not server.stopped:
                try:
                    server.stop()
                except HARNESS.StepFailed as error:
                    result.setdefault("cleanup_failures", []).append(HARNESS.redact(str(error))[:300])
    if current is not None:
        for ident in STEPS[STEPS.index(current) + 1 :]:
            if ident != "T16":
                record(result, ident, "NOT_RUN", why=f"{current} did not pass")
    hits = case.hits
    record(result, "T16", "FAIL" if hits else "PASS", why=json.dumps(hits)[:800] if hits else None)
    result["case"] = {
        "steps": case.steps,
        "first_failure": case.first_failure,
        "forbidden_error_hits": hits,
        "seconds": round(time.monotonic() - case.started, 1),
    }
    return case


def verdict(steps):
    states = [steps.get(ident, {}).get("status") for ident in STEPS]
    if all(state == "PASS" for state in states):
        return "SCENARIO_PASSED"
    return "SCENARIO_REFUSED" if "REFUSED" in states else "SCENARIO_FAILED"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--expect-version", required=True, help="Release N (X.Y.Z), or `published` for PyPI latest")
    parser.add_argument("--from-version", default="", help="Starting release; default the one before N on PyPI")
    parser.add_argument("--expect-wheel-sha256", default="", help="Optional: N's Core wheel must have this digest")
    parser.add_argument("--update-deadline", type=int, default=UPDATE_DEADLINE, help="Seconds B4 may take")
    parser.add_argument("--output", type=Path, default=Path("reports/acceptance/scenario.json"))
    args = parser.parse_args(argv)
    for name in ("expect_version", "from_version"):
        value = getattr(args, name)
        if value and value != "published" and not HARNESS.is_final_version(value):
            parser.error(f"--{name.replace('_', '-')} must be a final X.Y.Z version")
    if args.from_version == "published":
        parser.error("--from-version takes a version")
    if not 60 <= args.update_deadline <= 3600:
        parser.error("--update-deadline must be between 60 and 3600 seconds")
    return args


def write(path, result, secrets):
    HARNESS.write_result(path, result, secrets)


def main(argv=None):
    args = parse_args(argv)
    HARNESS.require_isolated_python()
    if sys.platform not in ("darwin", "linux"):
        sys.exit("This scenario targets macOS and Linux.")
    os.umask(0o077)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    guardian = HARNESS.load_vendor("guardian.py", HARNESS.GUARDIAN_SHA256)
    provenance = HARNESS.load_vendor("artifact_provenance.py", HARNESS.PROVENANCE_SHA256)
    parent = Path(tempfile.mkdtemp(prefix="hz-scenario-")).resolve(strict=True)
    info = parent.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        sys.exit("The proof root is not private.")
    result = {
        "schema": "acceptance-scenario/v1",
        "slice": 1,
        "advisory": True,
        "scenario_sha256": HARNESS.file_sha256(Path(__file__).resolve()),
        "harness_sha256": HARNESS.file_sha256(DIRECTORY / "run_acceptance.py"),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "ci": HARNESS.ci_record(),
        "proof_root": str(parent),
        "limits": [
            "ADVISORY: slice 1 of the end-to-end scenario; no release waits for this verdict yet.",
            "The servers run in the foreground; the daemon and the service manager are not involved (case 11 says "
            "why a hosted runner cannot prove an active daemon), so 'one owned backend' is the two web servers on "
            "one state folder.",
            "The age gates are set to 0 hours as a user can, so a release of minutes is installed by itself.",
            "The state folder is the user's default one inside the private home (HZ_STATE_DIR is not set): the "
            "updater treats any other state folder as development and never updates.",
            "No apps, no chat thread or bookmark before the update, no plugin upgrade check: later slices.",
        ],
        "verdict": "INCOMPLETE",
        "steps": {},
    }
    path = parent / "scenario.json"
    secrets = set()
    write(path, result, secrets)
    live_before = HARNESS.live_snapshot()
    ctx_base = {"Guarded": guardian["Guarded"], "check_runtime": provenance["check_runtime"]}
    case = None
    try:
        case = run_scenario(args, parent, result, ctx_base)
        secrets |= case.secrets
    except (HARNESS.StepFailed, OSError, ValueError, KeyError) as error:
        record(result, "P0", "FAIL", why=HARNESS.redact(f"{type(error).__name__}: {error}")[:500])
    live_after = HARNESS.live_snapshot()
    leaked = HARNESS.secrets_in_logs(case.root, secrets) if case else 0
    result["isolation"] = {
        "live_untouched": live_before == live_after,
        "logs_containing_session_tokens": leaked,
    }
    result["verdict"] = verdict(result["steps"])
    if leaked or live_before != live_after:
        result["verdict"] = "SCENARIO_REJECTED_ISOLATION"
    result["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    write(path, result, secrets)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, args.output)
    print(json.dumps({"scenario_verdict": result["verdict"], "result": str(args.output)}))
    return 0 if result["verdict"] == "SCENARIO_PASSED" else 1


def summary(path):
    """Markdown for the job summary; a missing or unreadable receipt says so instead of failing the step."""
    try:
        result = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return "## Acceptance scenario (advisory)\n\nNo scenario receipt was written.\n"
    lines = [f"## Acceptance scenario slice 1 (advisory): {result.get('verdict')}", "", "| Step | Status | Why |", "|---|---|---|"]
    for ident in STEPS:
        step = result.get("steps", {}).get(ident, {})
        why = str(step.get("why") or "").replace("|", "/").replace("\n", " ")[:300]
        lines.append(f"| {ident} | {step.get('status', 'NOT_RUN')} | {why} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    if sys.argv[1:2] == ["--summary"] and len(sys.argv) == 3:
        print(summary(sys.argv[2]))
        sys.exit(0)
    sys.exit(main())
