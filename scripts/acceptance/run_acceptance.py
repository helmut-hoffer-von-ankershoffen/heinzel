#!/usr/bin/env python3
"""Public installer acceptance for Heinzelmännchen. See README.md.

Stdlib only. Run isolated (`python3 -I`). Downloaded or installed code is never imported into this process.
"""

import argparse
import base64
import hashlib
import json
import os
import platform
import pwd
import re
import runpy
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
import zipfile
from email.parser import BytesParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
GUARDIAN_SHA256 = "1881539e7ebde1eac201741beb78135bb2b2c9165a0e2c61253060c228674c72"
PROVENANCE_SHA256 = "c4ceede1a1b8313a0c12154e7acbb4b71a59b6325ab445e25daf8ebf92c0b6c8"
LABEL = "io.github.helmut-hoffer-von-ankershoffen.heinzel.daemon"
INSTALL_URL = "https://heinzel.team/install.sh"
LITERAL_CURL = f"curl -LsSf {INSTALL_URL} | sh"
LITERAL_UVX = ["uvx", "heinzel@latest"]
COLLECTION = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins"
CORE_RAW = "https://raw.githubusercontent.com/helmut-hoffer-von-ankershoffen/heinzel"
LIVE_PORT = 8737
GENERIC = "app profile or one of its artifacts could not be verified"
QUIESCENT = "not proven quiescent"
HTTP403 = "HTTP 403"
SIGNATURES = (GENERIC, QUIESCENT, HTTP403)
READ_ONLY_MANAGER = {
    "print",
    "list",
    "managername",
    "manageruid",
    "is-active",
    "is-enabled",
    "show",
    "status",
    "cat",
    "list-units",
    "list-unit-files",
}
ANNOUNCE = re.compile(r"(http://[^\s/]+)/#token=([A-Za-z0-9_-]+)")
SYSTEMD_UNITS = ("hz-daemon.service", "hz-daemon.timer")
FINAL_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
RELEASE_WHEEL_URL = re.compile(
    r"https://github\.com/helmut-hoffer-von-ankershoffen/heinzel/releases/download/"
    r"(?:v(?=[0-9.]+/)|edge-v(?=[0-9.]+\.dev))(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:\.dev[0-9]+)?)"
    r"/heinzel-(?P=version)-py3-none-any\.whl"
)
CANDIDATE_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:\.dev[0-9]+)?")
RELEASE_PROFILE_URL = re.compile(
    r"https://github\.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/releases/download/"
    r"app-v(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:\.dev[0-9]+)?)/standard\.json"
)
PHASE_DEADLINE = 600
READY_DEADLINE = 600
SHORT_DEADLINE = 60
CASE_DEADLINE = 2400
MAX_DOWNLOAD = 128 * 1024 * 1024
CASES = {
    "1": "fresh_curl",
    "2": "fresh_uvx",
    "3": "retained_stopped_unit",
    "4": "versionless_reinstall",
    "5": "reported_error_sequence",
    "6": "update_from_previous_app",
    "7": "plugin_cli_lifecycle",
    "8": "plugin_ui_lifecycle",
    "9": "uninstall_reinstall_retention",
    "10": "update_with_stopped_unit",
    "11": "active_daemon_update",
    "12": "private_plugin",
    "13": "default_is_stable",
}
PUBLIC_CASES = ("1", "2", "3", "4", "5")
BOOTSTRAPS = ("literal", "candidate-launcher", "explicit", "candidate-app")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_LINUX = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
CANDIDATE_VERDICTS = {
    "FAIL": "CANDIDATE_REJECTED",
    "PARTIAL": "CANDIDATE_PARTIAL",
    "EXCEPT": "CANDIDATE_ACCEPTED_EXCEPT_UNPROVABLE",
    "OK": "CANDIDATE_ACCEPTED",
}
PASSING = {
    "ACCEPTED",
    "ACCEPTED_EXCEPT_UNPROVABLE",
    "PARTIAL_PASS",
    "CANDIDATE_ACCEPTED",
    "CANDIDATE_ACCEPTED_EXCEPT_UNPROVABLE",
    "CANDIDATE_PARTIAL",
}
OLD_STATUSES = ("UNPROVABLE", "SKIPPED")
LIMITS = [
    "REAL: public URLs, PyPI, GitHub release assets, uv, managed Python, foreground server, loopback HTTP, read-only "
    "service-manager queries (launchctl managername/manageruid/list on macOS, systemctl --user list-unit-files on Linux).",
    "SIMULATED: on macOS, daemon registration in cases 3 and 10 uses the installed SDK with an intercepted runner; the "
    "unit is never loaded. This is real manager absence, not active-daemon handover or restart proof. On Linux the "
    "unit steps of cases 3 and 10 are UNPROVABLE: the quiescence proof needs the user manager's private socket, which a "
    "hosted runner does not have.",
    "NOT COVERED: a user's own machine and its unknown state, updates from before the starting app, legacy or arbitrary "
    "units, Electron, Safari or Firefox, Windows.",
    "LIFECYCLE CASES 6-12: the browser is a real headless Chrome with a private profile, driven over the DevTools "
    "protocol (real mouse and key events); it is not a user's Chrome, not headed, and not Safari. A candidate "
    "(--candidate-wheel, or --candidate-wheel-url) proves candidate behaviour, never the public path: a launcher-only "
    "candidate run still installs the governed app's published Core.",
    "The update starts from the published earlier app installed through its explicit profile. No second machine, no "
    "earlier history, and no active service-manager daemon is involved (case 11 says why).",
    "A plugin from a private repository needs a sign-in; this harness has no credentials, so case 12 is UNPROVABLE.",
    "The service manager's user domain is shared with the host, so a private HOME does not isolate it; a registered "
    "label or unit, or on macOS a non-Aqua session, makes the case REFUSE rather than unload anything. Without a "
    "systemd user manager (a hosted Linux runner) the check is recorded as not applicable.",
    "install.sh is hashed and compared before the case, then the literal pipe fetches it again: a server change in "
    "between is undetectable. Astral's uv installer has no published pin here; only its hash is recorded.",
    "PATH spies catch only PATH-resolved launchctl/systemctl. An absolute-path mutation would be detected only "
    "through label presence afterwards. This is no sandbox against malicious packages or noncooperating OS actors.",
    "Output scans see each phase's last 64 KiB per stream, the guardian's bound.",
]


class StepFailed(Exception):
    """A checked assertion failed; the message is fixed text plus safe values."""


class Refused(Exception):
    """The host cannot give the evidence this case needs; nothing was changed to obtain it."""


class Skipped(Exception):
    """The case has nothing to run in this configuration; nothing failed."""


class Unprovable(Exception):
    """The evidence cannot be produced on this host; the reason is the result."""


def fail(message):
    raise StepFailed(message)


def check(condition, message):
    if not condition:
        fail(message)


EDGE_CANDIDATE = None
"""The one edge version an explicit candidate run accepts as Core; None on every public run, so no default can."""


def is_accepted_core(version):
    """A final version, or exactly the edge release this run was asked to prove."""
    return is_final_version(version) or (EDGE_CANDIDATE is not None and version == EDGE_CANDIDATE)


def is_final_version(version):
    """A release a default install may select: X.Y.Z, never a dev, pre, post or local version."""
    return isinstance(version, str) and FINAL_VERSION.fullmatch(version) is not None


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_sha256(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def load_vendor(name, expected):
    path = HERE / "vendor" / name
    if file_sha256(path) != expected:
        sys.exit(f"{name} does not match its reviewed hash; refusing to load it.")
    return runpy.run_path(str(path))


def redact(text):
    text = re.sub(r"#token=[^\s\"']+", "#token=<redacted>", text)
    text = re.sub(r"(?i)bearer\s+[^\s\"']+", "Bearer <redacted>", text)
    text = re.sub(r"(gh[pousr]_[A-Za-z0-9]{10,}|github_pat_[A-Za-z0-9_]{10,})", "<redacted>", text)
    return re.sub(r"(https?://[^\s?\"']+)\?[^\s\"']*", r"\1?<redacted>", text)


def tail(text, lines=6, chars=700):
    return redact("\n".join(text.strip().splitlines()[-lines:]))[-chars:]


def fetch(url, limit=MAX_DOWNLOAD, timeout=60):
    request = urllib.request.Request(url, headers={"User-Agent": "heinzel-acceptance"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS URLs
        if not response.url.startswith("https://"):
            fail("A published artifact redirected away from HTTPS")
        data = response.read(limit + 1)
    check(len(data) <= limit, "A published artifact exceeded its size bound")
    return data


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def latest_app_version():
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        opener.open(urllib.request.Request(f"{COLLECTION}/releases/latest", method="HEAD"), timeout=30)
    except urllib.error.HTTPError as error:
        location = error.headers.get("Location", "")
        error.close()
        match = re.fullmatch(re.escape(COLLECTION) + r"/releases/tag/app-v([0-9A-Za-z.]+)", location)
        if error.code in (301, 302, 307, 308) and match:
            check(
                is_final_version(match.group(1)), f"The latest app designation is not a final version: {match.group(1)}"
            )
            return match.group(1)
    fail("The governed latest app designation could not be read")
    return None


def wheel_version(wheel, name):
    wanted = name.replace("-", "_")
    with zipfile.ZipFile(wheel) as archive:
        found = [
            item for item in archive.namelist() if re.fullmatch(rf"{re.escape(wanted)}-[^/]+\.dist-info/METADATA", item)
        ]
        check(len(found) == 1, f"{name}: wheel metadata is missing")
        message = BytesParser().parsebytes(archive.read(found[0]))
    return message["Version"]


def fetch_artifacts(profile, app, accepted_dir):
    """Download every artifact of an app profile from its release, bind it to the profile and return what was bound."""
    root = f"{COLLECTION}/releases/download/app-v{app}/"
    bound = {}
    for artifact in profile["artifacts"]:
        check(
            is_accepted_core(artifact["version"])
            if artifact["name"] == "heinzel"
            else is_final_version(artifact["version"]),
            f"{artifact['name']}: {artifact['version']} is not a final version",
        )
        check(artifact["url"] == root + artifact["filename"], f"{artifact['name']}: URL is outside the app release")
        data = fetch(artifact["url"])
        check(sha256(data) == artifact["sha256"], f"{artifact['name']}: release asset fails its profile hash")
        path = accepted_dir / artifact["filename"]
        path.write_bytes(data)
        check(wheel_version(path, artifact["name"]) == artifact["version"], f"{artifact['name']}: version mismatch")
        bound[artifact["name"]] = {"version": artifact["version"], "sha256": artifact["sha256"]}
    return bound


def preflight(args, accepted_dir):
    """Bind every published input to its release identity. Executes nothing it downloads."""
    version, app = args.expect_version, args.expect_app_version
    report = {"expect_version": version, "expect_app_version": app}
    check(version == app and CANDIDATE_VERSION.fullmatch(version), "A candidate names one final or edge version")
    if not is_final_version(version):
        global EDGE_CANDIDATE  # noqa: PLW0603 - set once, before any case runs, for this explicit run only
        EDGE_CANDIDATE = version
    pypi = json.loads(fetch("https://pypi.org/pypi/heinzel/json", 8 * 1024 * 1024))
    report["pypi_latest"] = pypi["info"]["version"]
    check(pypi["info"]["version"] == version, f"PyPI latest is {pypi['info']['version']}, not expected {version}")
    wheels = [u for u in pypi["urls"] if u["packagetype"] == "bdist_wheel" and not u.get("yanked")]
    check(len(wheels) == 1, "PyPI does not list exactly one unyanked wheel")
    pypi_hash = wheels[0]["digests"]["sha256"]

    designated = latest_app_version()
    report["designated_app"] = designated
    check(designated == app, f"The designated latest app is {designated}, not expected {app}")

    profile_bytes = fetch(f"{COLLECTION}/releases/download/app-v{app}/standard.json", 1024 * 1024)
    report["profile_sha256"] = sha256(profile_bytes)
    if args.accept_profile_sha256:
        check(report["profile_sha256"] == args.accept_profile_sha256, "Profile differs from the accepted hash")
    profile = json.loads(profile_bytes)
    check(profile.get("format") == 2, "The profile is not format 2")
    check(profile.get("core_version") == version, "The profile selects a different Core: a stale app is a failure")
    core = [a for a in profile["artifacts"] if a["name"] == "heinzel"]
    check(len(core) == 1 and core[0]["sha256"] == pypi_hash, "The profile's Core differs from PyPI's wheel")
    if args.accept_wheel_sha256:
        check(pypi_hash == args.accept_wheel_sha256, "PyPI's wheel differs from the accepted hash")

    accepted_dir.mkdir()
    (accepted_dir / "standard.json").write_bytes(profile_bytes)
    report["artifacts"] = fetch_artifacts(profile, app, accepted_dir)
    pypi_bytes = fetch(wheels[0]["url"])
    check(sha256(pypi_bytes) == pypi_hash, "PyPI's wheel bytes fail PyPI's digest")
    report["core_wheel_sha256"] = pypi_hash

    live = sha256(fetch(INSTALL_URL, 1024 * 1024))
    tagged = sha256(fetch(f"{CORE_RAW}/v{version}/docs/install.sh", 1024 * 1024))
    report["install_sh"] = {"live_sha256": live, "tag_sha256": tagged}
    check(
        live == tagged or live == args.accept_install_sh_sha256,
        "The live install.sh differs from the release tag and from any accepted hash",
    )
    report["uv_installer_sha256_unpinned"] = sha256(fetch("https://astral.sh/uv/install.sh", 4 * 1024 * 1024))
    return report, profile


def manager_gate():
    """Fixed read-only service-manager evidence; anything but a verified absence refuses. Returns what was seen."""
    if sys.platform == "darwin":
        return launchd_gate()
    return systemd_gate()


def launchd_gate():
    env = {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": pwd.getpwuid(os.getuid()).pw_dir}

    def query(*args):
        try:
            done = subprocess.run(
                ["/bin/launchctl", *args], capture_output=True, text=True, timeout=10, check=False, env=env
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise Refused("launchctl inspection was unavailable") from error
        if done.returncode:
            raise Refused(f"launchctl {args[0]} failed")
        return done.stdout

    name, uid = query("managername").strip(), query("manageruid").strip()
    if name != "Aqua" or uid != str(os.getuid()):
        raise Refused(f"Not a verified Aqua session for this user (manager {name!r})")
    lines = query("list").splitlines()
    if not lines or lines[0].split() != ["PID", "Status", "Label"]:
        raise Refused("launchctl list was malformed")
    labels = set()
    for line in lines[1:]:
        parts = line.split()
        if len(parts) != 3 or (parts[0] != "-" and not parts[0].isdigit()) or parts[2] in labels:
            raise Refused("launchctl list was malformed")
        labels.add(parts[2])
    if LABEL in labels:
        raise Refused(f"A real Heinzel daemon label is registered ({LABEL}); nothing was unloaded")
    if name != query("managername").strip() or uid != query("manageruid").strip():
        raise Refused("The launchd session changed during inspection")
    return {"manager": "launchd", "session": name, "applicable": True}


def systemd_gate():
    """The systemd user manager, read only. No user manager (a hosted runner) is recorded as not applicable."""
    systemctl = next((c for c in ("/usr/bin/systemctl", "/bin/systemctl") if Path(c).is_file()), None)
    if systemctl is None:
        return {"manager": "systemd-user", "applicable": False, "why": "systemctl is not installed"}
    env = {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": pwd.getpwuid(os.getuid()).pw_dir}
    env.update({key: os.environ[key] for key in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS") if key in os.environ})
    try:
        done = subprocess.run(
            [systemctl, "--user", "--no-pager", "list-unit-files", "--no-legend", *SYSTEMD_UNITS],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise Refused("systemctl inspection was unavailable") from error
    registered = sorted(
        {line.split()[0] for line in done.stdout.splitlines() if line.split()[:1] and line.split()[0] in SYSTEMD_UNITS}
    )
    if registered:
        raise Refused(f"A real Heinzel daemon unit is registered ({', '.join(registered)}); nothing was stopped")
    if done.returncode and not done.stdout.strip():
        reason = done.stderr.strip().lower()
        if not reason:
            return {"manager": "systemd-user", "applicable": True, "units_registered": []}
        if any(word in reason for word in ("bus", "xdg_runtime_dir", "no medium", "not been booted", "connect")):
            return {"manager": "systemd-user", "applicable": False, "why": "no systemd user manager on this host"}
        raise Refused("systemctl list-unit-files failed")
    return {"manager": "systemd-user", "applicable": True, "units_registered": []}


def live_snapshot():
    """Read-only identity of what must stay untouched. Never contacts the live port."""
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)

    def stamp(path):
        try:
            info = path.lstat()
        except OSError:
            return None
        return [info.st_ino, info.st_mtime_ns]

    lsof = shutil.which("lsof", path="/usr/sbin:/usr/bin:/bin")
    try:
        pids = (
            subprocess.run(
                [lsof, "-nP", f"-iTCP:{LIVE_PORT}", "-sTCP:LISTEN", "-t"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            ).stdout.split()
            if lsof
            else None
        )
    except (OSError, subprocess.SubprocessError):
        pids = None
    units = (
        [home / "Library/LaunchAgents" / f"{LABEL}.plist"]
        if sys.platform == "darwin"
        else [home / ".config/systemd/user" / unit for unit in SYSTEMD_UNITS]
    )
    return {
        "live_port_listener_pids": pids,
        "real_tool_receipt": stamp(home / ".local/share/uv/tools/heinzel/uv-receipt.toml"),
        "real_hz_shim": stamp(home / ".local/bin/hz"),
        "real_daemon_units": {unit.name: stamp(unit) for unit in units},
    }


class Case:
    def __init__(self, ident, parent, ctx):
        self.ident, self.name, self.ctx = ident, CASES[ident], dict(ctx)
        self.expect_version = ctx["args"].expect_version
        self.plugins_expected = None  # distribution -> {"enabled": bool}; None means the selected profile's defaults
        self.core_wheel_override = None
        self.notes = {}
        self.root = parent / f"case{ident}-{self.name}"
        self.root.mkdir(mode=0o700)
        self.root = self.root.resolve(strict=True)
        self.started = time.monotonic()
        self.steps, self.hits, self.secrets = [], [], set()
        self.first_failure = None
        self.status = "RUNNING"
        self.seq = 0
        for name in ("home", "tmp", "logs", "guard", "tools", "bin"):
            (self.root / name).mkdir(mode=0o700)
        self.tools, self.bin = self.root / "tools", self.root / "bin"
        self.prefix = self.tools / "heinzel"
        self.denied = self.root / "manager-action-denied"
        for name in ("launchctl", "systemctl"):
            spy = self.root / "guard" / name
            spy.write_text(
                "#!/bin/sh\n"
                'verb="$1"\n'
                'for arg in "$@"; do case "$arg" in -*) ;; *) verb="$arg"; break ;; esac; done\n'
                f"printf '%s\\n' \"$verb\" >> '{self.denied}'\nexit 126\n"
            )
            spy.chmod(0o700)
        self.env = {key: os.environ[key] for key in ("LANG", "LC_ALL", "TERM") if key in os.environ}
        self.env.update(
            HOME=str(self.root / "home"),
            TMPDIR=str(self.root / "tmp"),
            PATH=os.pathsep.join([str(self.root / "guard"), str(self.bin), "/usr/bin", "/bin"]),
            UV_NO_CONFIG="1",
            UV_CACHE_DIR=str(self.root / "uv-cache"),
            UV_PYTHON_INSTALL_DIR=str(self.root / "uv-python"),
            UV_TOOL_DIR=str(self.tools),
            UV_TOOL_BIN_DIR=str(self.bin),
            XDG_CACHE_HOME=str(self.root / "xdg-cache"),
            XDG_CONFIG_HOME=str(self.root / "xdg-config"),
            XDG_DATA_HOME=str(self.root / "xdg-data"),
            XDG_STATE_HOME=str(self.root / "xdg-state"),
            BROWSER="true",
            NO_COLOR="1",
            FORCE_COLOR="0",
            HZ_UPDATE_CHECK="false",
            HZ_SYSTEM_CREDENTIAL__BACKEND="environment",
            HZ_SYSTEM_DAEMON__UNIT_DIR=str(self.root / "units"),
        )
        for key in ("CONFIG_HOME", "DATA_DIR", "CACHE_DIR", "STATE_DIR", "LOG_DIR"):
            self.env["HZ_" + key] = str(self.root / key.lower())
        if ctx["explicit_profile"]:
            self.env["HZ_APP_PROFILE"] = ctx["explicit_profile"]

    def provide_uv(self):
        """Case 1 omits this: there uv must really be absent."""
        for name in ("uv", "uvx"):
            found = shutil.which(name)
            check(found, f"{name} is required for this case")
            (self.root / "guard" / name).symlink_to(Path(found).resolve(strict=True))

    def step(self, name, action):
        started = time.monotonic()
        record = {"name": name, "ok": False}
        self.steps.append(record)
        try:
            if time.monotonic() - self.started > CASE_DEADLINE:
                fail("The case deadline passed")
            manager_gate()
            result = action()
            manager_gate()
            self.check_denied()
            record["ok"] = True
            if result is not None:
                record["evidence"] = result
            return result
        except Refused as error:
            record["refused"] = str(error)
            raise
        except (Skipped, Unprovable) as error:
            record["ok"], record["note"] = True, str(error)
            raise
        except StepFailed as error:
            record["failure"] = redact(str(error))
            self.first_failure = self.first_failure or {"step": name, "failure": record["failure"]}
            raise
        except Exception as error:
            record["failure"] = redact(f"{type(error).__name__}: {error}")[:500]
            self.first_failure = self.first_failure or {"step": name, "failure": record["failure"]}
            raise StepFailed(record["failure"]) from error
        finally:
            record["seconds"] = round(time.monotonic() - started, 1)

    def check_denied(self):
        if self.denied.exists():
            used = self.denied.read_text().split()
            mutating = sorted({word for word in used if word not in READ_ONLY_MANAGER})
            check(not mutating, f"A service-manager mutation was attempted: {mutating}")

    def write_log(self, label, stdout, stderr):
        self.seq += 1
        path = self.root / "logs" / f"{self.seq:02d}-{label}.log"
        body = redact(f"--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(body)
        return path.name

    def scan(self, label, text, allowed=()):
        found = [signature for signature in SIGNATURES if signature in text and signature not in allowed]
        for signature in found:
            self.hits.append({"phase": label, "signature": signature})
        allowed_seen = [signature for signature in allowed if signature in text]
        return found, allowed_seen

    def _finish(self, process):
        stop_error = None
        try:
            process.stop()
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            stop_error = type(error).__name__ + ": " + str(error)[:120]
        out = process.out["stdout"].decode(errors="replace")
        err = process.out["stderr"].decode(errors="replace")
        return out, err, stop_error

    def run(self, argv, label, deadline=PHASE_DEADLINE, expect=0, allowed=()):
        """Bounded owned command. expect: exit code, or "nonzero", or None to record only."""
        process = self.ctx["Guarded"](list(argv), cwd=self.root, env=self.env)
        status, timed_out = None, False
        try:
            status = process.wait(deadline)
        except subprocess.TimeoutExpired:
            timed_out = True
        out, err, stop_error = self._finish(process)
        log = self.write_log(label, out, err)
        found, _ = self.scan(label, out + err, allowed)
        check(not stop_error, f"{label}: cleanup is uncertain ({stop_error})")
        check(not timed_out, f"{label}: exceeded {deadline}s (log {log})")
        check(not found, f"{label}: forbidden error {found}: {tail(err)}")
        if expect == "nonzero":
            check(status != 0, f"{label}: was expected to refuse but succeeded")
        elif expect is not None:
            check(status == expect, f"{label}: exit {status}, expected {expect}: {tail(err or out)}")
        return status, out, err

    def json_command(self, *args, label):
        _, out, _ = self.run([str(self.bin / "hz"), *args, "--json"], label, SHORT_DEADLINE * 2)
        return json.loads(out)

    def foreground(self, argv, label, allowed=()):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        check(port != LIVE_PORT, "The ephemeral port collided with the live app's port")
        env = self.env | {"HZ_UI_PORT": str(port)}
        process = self.ctx["Guarded"](list(argv), cwd=self.root, env=env)
        deadline = time.monotonic() + READY_DEADLINE
        base = token = None
        error = None
        try:
            while time.monotonic() < deadline and process.status is None:
                process.pump(0.2)
                text = process.out["stdout"].decode(errors="replace") + process.out["stderr"].decode(errors="replace")
                match = ANNOUNCE.search(text)
                if match:
                    base, token = match.groups()
                    self.secrets.add(token)
                    break
        except RuntimeError as exc:
            error = str(exc)
        if token is None:
            status = process.status
            out, err, stop_error = self._finish(process)
            log = self.write_log(label, out, err)
            found, _ = self.scan(label, out + err, allowed)
            reason = "exited" if status is not None else "did not announce in time"
            fail(
                f"{label}: {reason} (exit {status}, forbidden {found}, guardian {error or stop_error}, log {log}): "
                f"{tail(err or out)}"
            )
        if base != f"http://127.0.0.1:{port}":
            self._finish(process)
            fail(f"{label}: announced an unexpected address; it was not contacted")
        return Session(self, process, label, base, token, port, allowed)

    def verify_cleanup(self, port, pid):
        deadline = time.monotonic() + 15
        while True:
            lines = subprocess.run(
                ["/bin/ps", "-axo", "command="], capture_output=True, text=True, timeout=15, check=False
            ).stdout.splitlines()
            leftovers = sum(str(self.root) in line for line in lines)
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    listening = True
            except OSError:
                listening = False
            try:
                os.killpg(pid, 0)
                group = True
            except ProcessLookupError:
                group = False
            except PermissionError:
                group = True
            if not (leftovers or listening or group) or time.monotonic() > deadline:
                break
            time.sleep(0.5)
        check(not leftovers, f"{leftovers} owned process(es) survived cleanup; they were not touched further")
        check(not listening, "The ephemeral port still accepts connections after cleanup")
        check(not group, "The owned process group survived cleanup")


class Session:
    def __init__(self, case, process, label, base, token, port, allowed):
        self.case, self.process, self.label = case, process, label
        self.base, self.token, self.port, self.allowed = base, token, port, allowed
        self.stopped = False

    def http(self, path, *, auth=True):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        headers = {"Authorization": "Bearer " + self.token} if auth else {}
        request = urllib.request.Request(self.base + path, headers=headers)
        try:
            with opener.open(request, timeout=30) as response:
                return response.status, response.read(64 * 1024 * 1024)
        except urllib.error.HTTPError as error:
            body = error.read(4096)
            error.close()
            return error.code, body

    def stop(self):
        if self.stopped:
            return None
        self.stopped = True
        pid = self.process.process.pid
        pending = self.process.status
        out, err, stop_error = self.case._finish(self.process)
        log = self.case.write_log(self.label, out, err)
        found, _ = self.case.scan(self.label, out + err, self.allowed)
        check(not stop_error, f"{self.label}: cleanup is uncertain ({stop_error})")
        check(not found, f"{self.label}: forbidden error {found}: {tail(err)}")
        check(pending is None, f"{self.label}: the foreground app had exited before it was stopped ({pending})")
        status = self.process.status
        check(status in (0, 130, -2), f"{self.label}: foreground stopped with {status} (None: needed SIGKILL), {log}")
        self.case.verify_cleanup(self.port, pid)
        return {"foreground_stop_status": status, "log": log}


def site_packages(prefix):
    sites = list((prefix / "lib").glob("python*/site-packages"))
    check(len(sites) == 1, "The installed package directory is unavailable")
    return sites[0]


def compare_wheel(wheel, site, name, version, prefix):
    """Every non-metadata file of the accepted wheel must be byte-identical in this case's environment."""
    wanted = name.replace("-", "_")
    metadata = list(site.glob(f"{wanted}-*.dist-info/METADATA"))
    check(len(metadata) == 1, f"{name}: installed metadata is unavailable")
    message = BytesParser().parsebytes(metadata[0].read_bytes())
    check(message["Version"] == version, f"{name}: installed version {message['Version']}, expected {version}")
    count = 0
    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            if info.is_dir() or ".dist-info/" in info.filename or ".data/" in info.filename:
                continue
            path = site.joinpath(*info.filename.split("/"))
            check(
                not path.is_symlink() and path.is_file() and path.resolve().is_relative_to(prefix),
                f"{name}: {info.filename} is not a regular file in this environment",
            )
            check(path.read_bytes() == archive.read(info), f"{name}: {info.filename} differs from the accepted wheel")
            count += 1
    check(count, f"{name}: the wheel carries no runtime files")
    return count


def verify_files(case, *, profile=None, accepted=None, receipt_profile=None, plugins=True, extra_plugins=()):
    """Bytes, receipts and interpreter, before any installed code is asked anything.

    After an update, `profile`/`accepted` describe what is installed now and `receipt_profile` what the standard-app
    receipt and cached wheels still describe; `plugins=False` compares only the Core. `extra_plugins` are
    distributions added after the installation and are not expected in the profile.
    """
    ctx = case.ctx
    profile, accepted = profile or ctx["profile"], accepted or ctx["accepted"]
    receipt_profile = receipt_profile or profile
    prefix = case.prefix.resolve(strict=True)
    check(prefix.is_relative_to(case.root), "The tool environment is outside this case")
    core = next(a for a in profile["artifacts"] if a["name"] == "heinzel")
    runtime = ctx["check_runtime"](accepted / core["filename"], case.prefix, case.root)
    site = site_packages(prefix)
    files = {}
    for artifact in profile["artifacts"] if plugins else [core]:
        files[artifact["name"]] = compare_wheel(
            accepted / artifact["filename"], site, artifact["name"], artifact["version"], prefix
        )
    for artifact in receipt_profile["artifacts"]:
        cached = case.root / "data_dir/app-artifacts" / artifact["sha256"] / artifact["filename"]
        check(cached.is_file() and not cached.is_symlink(), f"{artifact['name']}: cached wheel is missing")
        check(file_sha256(cached) == artifact["sha256"], f"{artifact['name']}: cached wheel fails its digest")
    receipt_path = case.root / "data_dir/standard-app.json"
    check(receipt_path.is_file(), "The standard-app receipt is missing")
    receipt = json.loads(receipt_path.read_bytes())
    check(receipt.get("format") == 2, "The receipt format is unsupported")
    check(receipt.get("core_version") == receipt_profile["core_version"], "The receipt selects a different Core")
    want = {(a["name"], a["version"], a["sha256"]) for a in receipt_profile["artifacts"]}
    have = {(a["name"], a["version"], a["sha256"]) for a in receipt.get("artifacts", [])}
    check(want == have, "The receipt's distributions differ from the published profile")

    managed = (case.root / "uv-python").resolve(strict=True)
    python = (prefix / "bin/python").resolve(strict=True)
    check(python.is_relative_to(managed), "The tool does not use this case's managed Python")
    tool = tomllib.loads((prefix / "uv-receipt.toml").read_text())["tool"]
    pending = case.root / "state_dir/daemon/standard-install-pending.json"
    check(not os.path.lexists(pending), "An installer transaction is still pending")
    return {
        "runtime_files": runtime,
        "wheel_files_identical": files,
        "receipt_sha256": file_sha256(receipt_path),
        "managed_python_in_case": True,
        "startup_receipt_requirements": len(tool.get("requirements", [])),
    }


def _plugins_match(case, plugins):
    wanted = {a["name"]: a["version"] for a in case.ctx["profile"]["artifacts"] if a["name"] != "heinzel"}
    expected = case.plugins_expected
    if expected is None:
        expected = {name: {"enabled": True, "version": version} for name, version in wanted.items()}
    names = {p["distribution"] for p in plugins}
    required = {name for name, want in expected.items() if not want.get("optional")}
    check(
        required <= names <= set(expected),
        f"Plugin inventory differs from the expectation: {sorted(names ^ required)}",
    )
    for plugin in plugins:
        name, want = plugin["distribution"], expected[plugin["distribution"]]
        check(
            plugin.get("enabled") is want["enabled"], f"Plugin {name} enabled={plugin.get('enabled')}, expected {want}"
        )
        check(not plugin.get("error"), f"Plugin {name} reports an error")
        if plugin.get("version") is not None and want.get("version"):
            check(plugin["version"] == want["version"], f"{name} runs {plugin['version']}, expected {want['version']}")
    return sorted(names)


def core_wheel(case):
    if case.core_wheel_override:
        return case.core_wheel_override
    core = next(a for a in case.ctx["profile"]["artifacts"] if a["name"] == "heinzel")
    return case.ctx["accepted"] / core["filename"]


def verify_http(case, session):
    version = case.expect_version
    status, _ = session.http("/api/system/info", auth=False)
    check(status in (401, 403), f"Unauthenticated API request returned {status}")
    status, body = session.http("/api/system/info")
    check(status == 200, f"Authenticated identity request returned {status}")
    info = json.loads(body)
    check(info["version"] == version, f"The server runs {info['version']}, expected {version}")
    check(Path(info["python_executable"]).is_relative_to(case.prefix), "The server runs outside this case's prefix")
    check(not info["plugin_errors"], "The server reports plugin errors")
    status, body = session.http("/api/system/plugin/list")
    check(status == 200, f"Plugin inventory request returned {status}")
    plugins = _plugins_match(case, json.loads(body))

    assets = {}
    with zipfile.ZipFile(core_wheel(case)) as archive:
        status, html = session.http("/")
        check(status == 200, f"The packaged frontend returned {status}")
        index = html.decode(errors="replace")
        refs = sorted(set(re.findall(r"""(?:src|href)=["'](/?assets/[^"'?#]+)""", index)))
        check(any(r.endswith(".js") for r in refs), "The served page references no JavaScript asset")
        check(any(r.endswith(".css") for r in refs), "The served page references no stylesheet")
        for ref in refs:
            path = ref.lstrip("/")
            status, body = session.http("/" + path)
            check(status == 200, f"Asset {path} returned {status}")
            expected = archive.read("heinzel/heads/web/static/" + path)
            check(body == expected, f"Served asset {path} differs from the accepted wheel")
            assets[path] = sha256(body)
    return {
        "loopback_port_reserved": True,
        "unauthenticated_rejected": True,
        "http_version": info["version"],
        "http_plugins": plugins,
        "frontend_assets_identical": assets,
    }


CHECKPOINT = """
from heinzel import Progress
from heinzel.kernel import Installation
print(Installation(Progress()).startup_state_matches())
"""


def verify_cli(case, *, retained=False):
    version = case.expect_version
    python = case.prefix / "bin/python"
    _, out, _ = case.run([str(python), "-I", "-c", CHECKPOINT], "startup-checkpoint", SHORT_DEADLINE)
    check(out.strip().splitlines()[-1] == "True", "The startup checkpoint does not match the installation")
    _, out, err = case.run([str(case.bin / "hz"), "--version"], "hz-version", SHORT_DEADLINE)
    check(version in out + err, f"`hz --version` does not report {version}")
    info = case.json_command("system", "info", label="hz-system-info")
    check(info["version"] == version, f"hz reports {info['version']}, expected {version}")
    check(Path(info["python_executable"]).is_relative_to(case.prefix), "hz runs outside this case's prefix")
    check(not info["plugin_errors"], "hz reports plugin errors")
    plugins = _plugins_match(case, case.json_command("system", "plugin", "list", label="hz-plugin-list"))
    daemon = case.json_command("system", "daemon", "status", label="hz-daemon-status")
    check(not daemon.get("loaded"), "The daemon reports loaded")
    check(bool(daemon.get("installed")) == retained, f"Daemon unit presence is not the expected {retained}")
    sharing = case.json_command("tailscale", "status", label="hz-sharing-status")
    check(sharing.get("desired_enabled") is False, "Sharing was enabled by the installation")
    sound = case.json_command("system", "sound", "list", label="hz-sound-list")
    check(sound.get("selected") == sound.get("effective") == "off", "Playback was enabled by the installation")
    return {"cli_version": info["version"], "cli_plugins": plugins, "startup_checkpoint": True}


def app_cycle(case, name, command, *, retained=False):
    """Run a public command to its foreground app, verify everything, then stop it and prove cleanup."""
    session = case.step(f"{name}: public command reaches foreground app", lambda: _start(case, name, command))
    holder = session["session"]
    del session["session"]
    try:
        case.step(f"{name}: installed bytes, receipt, managed Python", lambda: verify_files(case))
        case.step(f"{name}: HTTP identity, plugins, frontend assets", lambda: verify_http(case, holder))
    finally:
        case.step(f"{name}: stop owned foreground app", holder.stop)
    case.step(f"{name}: CLI identity, checkpoint, nothing enabled", lambda: verify_cli(case, retained=retained))


def _start(case, name, command):
    session = case.foreground(command, name.replace(" ", "-"))
    return {"session": session, "port_verified": True}


def shell_literal(command):
    return ["/bin/sh", "-c", command]


def snapshot(case, paths):
    return {str(path): path.read_bytes() for path in paths}


def retained_paths(case):
    config = sorted(p for p in (case.root / "config_home").rglob("*") if p.is_file())
    check(config, "No configuration exists to retain")
    plist = case.root / "units" / f"{LABEL}.plist"
    daemon = case.root / "state_dir/daemon"
    return [plist, daemon / "install.json", daemon / "paused", *config]


def uninstall(case, label):
    _, out, err = case.run(["uv", "tool", "uninstall", "heinzel"], label, SHORT_DEADLINE * 2)
    check(not os.path.lexists(case.prefix), "The Core prefix survived `uv tool uninstall`")
    for shim in ("hz", "heinzel", "hz-install"):
        check(not os.path.lexists(case.bin / shim), f"The {shim} shim survived `uv tool uninstall`")
    return {"uninstalled": tail(out + err, lines=1, chars=120)}


FIXTURE = """
import json, shutil, subprocess, sys
from datetime import timedelta
from pathlib import Path
from heinzel import Config, Progress, Settings
from heinzel.kernel import Installation, boot
from heinzel.modules.system.daemon import DaemonService, DaemonSettings

action, binary = sys.argv[1], Path(sys.argv[2])
uv = shutil.which("uv")
calls = []

def runner(command, *_args, **_kwargs):
    if command == [uv, "tool", "dir", "--bin"]:
        return subprocess.run(command, capture_output=True, text=True, check=False, timeout=15)
    calls.append(command)  # Never executed: this is the interception.
    return subprocess.CompletedProcess(command, 0, "", "")

config = Config.load(Path.home())
installation = Installation(Progress(), prefix=Path(sys.prefix), uv=uv)
service = DaemonService(
    boot(), config.settings(Settings, "hz"), config.settings(DaemonSettings, "system.daemon"),
    runner=runner, which=lambda name: uv if name == "uv" else "/intercepted/" + name,
    quiescence_installation=installation, continuity_fingerprint=installation.startup_fingerprint,
)
if action == "install":
    service.install(timedelta(minutes=5))
    service.pause()
    print(json.dumps({"calls": calls}))
else:
    accepted = service._quiescent_snapshot(binary)
    print(json.dumps({"accepted": accepted, "problem": getattr(service, "_quiescence_problem", None), "calls": calls}))
"""


def fixture(case, action, label):
    python = str(case.prefix / "bin/python")
    _, out, _ = case.run([python, "-I", "-c", FIXTURE, action, str(case.bin)], label, SHORT_DEADLINE * 2)
    return json.loads(out.strip().splitlines()[-1])


def install_fixture(case):
    result = fixture(case, "install", "fixture-install-intercepted")
    uid = os.getuid()
    plist = case.root / "units" / f"{LABEL}.plist"
    expected = [
        ["/intercepted/launchctl", "bootout", f"gui/{uid}/{LABEL}"],
        ["/intercepted/launchctl", "bootstrap", f"gui/{uid}", str(plist)],
    ]
    check(result["calls"] == expected, "The installed SDK asked for unexpected registration commands")
    check(plist.is_file(), "The fixture unit was not written inside this case")
    return {"intercepted_commands_validated": len(expected), "executed": 0, "registration": "simulated"}


def quiescence(case, label, *, accepted):
    result = fixture(case, "check", label)
    check(not result["calls"], "A read-only quiescence check asked for a manager mutation")
    check(result["accepted"] is accepted, f"Quiescence {result['accepted']}, expected {accepted}: {result['problem']}")
    return {"accepted": result["accepted"], "problem": None if accepted else result["problem"]}


def malformed(plist):
    plist.write_bytes(plist.read_bytes() + b"<!-- altered by acceptance negative control -->\n")


def public_cycle(case, name, *, retained=False):
    """The public `uvx` path; a release-candidate run substitutes the candidate launcher and its explicit profile."""
    public = case.ctx["public"]
    case.env.update(public["env"])
    try:
        app_cycle(case, name, public["command"], retained=retained)
    finally:
        for key in public["env"]:
            case.env.pop(key, None)


def skip_for_candidate(case, why):
    if case.ctx["public"]["mode"] == "release-candidate":
        raise Skipped(f"{why} A release-candidate run cannot prove the public path.")


def case_fresh_curl(case):
    skip_for_candidate(case, "The curl installer fetches the published app.")

    def uv_absent():
        env_path = case.env["PATH"].split(os.pathsep)
        for folder in [*env_path, str(case.root / "home/.local/bin")]:
            check(not (Path(folder) / "uv").exists(), f"uv is not absent from the private PATH ({folder})")
        for tool in ("curl", "sh"):
            check(shutil.which(tool, path=case.env["PATH"]), f"{tool} is missing from the private PATH")
        return {"uv_absent_from_private_path": True}

    case.step("uv absent from private PATH", uv_absent)
    case.env["PATH"] = str(case.root / "home/.local/bin") + os.pathsep + case.env["PATH"]
    app_cycle(case, "literal curl", shell_literal(LITERAL_CURL))

    def uv_private():
        uv = case.root / "home/.local/bin/uv"
        check(uv.is_file(), "The installer did not place uv in the private HOME")
        version = subprocess.run([str(uv), "--version"], capture_output=True, text=True, timeout=15, check=False)
        return {"uv_installed_privately": True, "uv_version": version.stdout.strip()}

    case.step("uv was installed inside the private HOME", uv_private)


def case_fresh_uvx(case):
    case.provide_uv()
    public_cycle(case, "literal uvx")


def case_retained(case):
    case.provide_uv()
    plist = case.root / "units" / f"{LABEL}.plist"
    public_cycle(case, "fresh uvx")
    case.step("write configuration to retain", lambda: _set_config(case))
    if sys.platform != "darwin":
        raise Unprovable(
            "The daemon unit steps need the quiescence proof, which reads the systemd user manager's private socket; "
            "a hosted Linux runner has none, and nothing is registered to obtain one."
        )
    case.step("generate trusted unit with intercepted registration", lambda: install_fixture(case))
    case.step("capture retained bytes", lambda: _capture(case))
    before = snapshot(case, retained_paths(case))
    original = plist.read_bytes()
    case.step("installed SDK accepts trusted unit", lambda: quiescence(case, "quiescence-trusted", accepted=True))

    def negative():
        malformed(plist)
        try:
            return quiescence(case, "quiescence-malformed", accepted=False)
        finally:
            plist.write_bytes(original)

    case.step("negative control: malformed unit denied (read-only)", negative)
    case.step("restored unit accepted again", lambda: quiescence(case, "quiescence-restored", accepted=True))
    case.step("uv tool uninstall removes prefix and shims", lambda: uninstall(case, "uv-tool-uninstall"))

    def preserved(where):
        check(snapshot(case, retained_paths(case)) == before, f"Retained files changed {where}")
        return {"retained_files_unchanged": len(before)}

    case.step("retained files survive uninstall", lambda: preserved("during uninstall"))

    def negative_reinstall():
        malformed(plist)
        try:
            public = case.ctx["public"]
            case.env.update(public["env"])
            try:
                _, _, err = case.run(
                    public["command"], "uvx-malformed-refusal", READY_DEADLINE, "nonzero", (QUIESCENT,)
                )
            finally:
                for key in public["env"]:
                    case.env.pop(key, None)
        finally:
            plist.write_bytes(original)
        check(not os.path.lexists(case.prefix), "The refused reinstall left a tool environment")
        predicate = "retained daemon unit or install receipt" in err
        check(predicate, f"The refusal lacks the predicate-specific reason: {tail(err)}")
        return {"refused": True, "predicate_specific": predicate}

    # The bytes are restored either way, so a weak refusal must not hide what the trusted reinstall does.
    control_failed = False
    try:
        case.step("negative control: malformed unit refused by public command", negative_reinstall)
    except StepFailed:
        control_failed = True
    public_cycle(case, "versionless reinstall", retained=True)
    case.step("retained files survive reinstall", lambda: preserved("during reinstall"))
    if control_failed:
        raise StepFailed("The public negative control failed")


def _set_config(case):
    case.json_command("system", "config", "set", "clock.zones", '["Europe/Berlin","Asia/Tokyo"]', label="hz-config-set")
    return {"configured": True}


def _capture(case):
    return {Path(k).name: sha256(v) for k, v in snapshot(case, retained_paths(case)).items()}


def case_reinstall(case):
    case.provide_uv()
    public_cycle(case, "fresh uvx")
    case.step("write configuration to retain", lambda: _set_config(case))
    config = sorted(p for p in (case.root / "config_home").rglob("*") if p.is_file())
    before = snapshot(case, config)
    case.step("uv tool uninstall removes prefix and shims", lambda: uninstall(case, "uv-tool-uninstall"))
    public_cycle(case, "versionless reinstall")

    def preserved():
        check(snapshot(case, config) == before, "Configuration changed across the reinstall")
        return {"configuration_unchanged": len(before)}

    case.step("configuration survives reinstall", preserved)


def case_sequence(case):
    """The reported commands in order. Every step runs even after a failure, so the whole sequence is recorded."""
    skip_for_candidate(case, "The sequence runs the literal public commands.")
    case.provide_uv()

    def foreground(name, command):
        def action():
            session = case.foreground(command, name)
            return session.stop()

        return action

    def not_found():
        status, _, _ = case.run(shell_literal("hz"), "hz-absent", SHORT_DEADLINE, 127)
        return {"exit": status}

    def bootstrap_help():
        _, out, err = case.run([*LITERAL_UVX, "--help"], "uvx-help", PHASE_DEADLINE)
        return {"help_shown": bool(out + err)}

    def documented():
        case.run(["uvx", "--from", "heinzel", "hz-install"], "uvx-hz-install", PHASE_DEADLINE)
        return {"exit": 0}

    sequence = [
        ("documented uvx --from heinzel hz-install", documented),
        ("literal curl, first", foreground("literal-curl-1", shell_literal(LITERAL_CURL))),
        ("uv tool uninstall", lambda: uninstall(case, "uv-tool-uninstall-1")),
        ("hz is gone", not_found),
        ("literal curl after uninstall", foreground("literal-curl-2", shell_literal(LITERAL_CURL))),
        ("uv tool uninstall again", lambda: uninstall(case, "uv-tool-uninstall-2")),
        ("literal uvx after uninstall", foreground("literal-uvx-1", LITERAL_UVX)),
        ("literal uvx repeated", foreground("literal-uvx-2", LITERAL_UVX)),
        ("literal uvx --help", bootstrap_help),
    ]
    failed = False
    for name, action in sequence:
        try:
            case.step(name, action)
        except StepFailed:
            failed = True
    if failed:
        raise StepFailed("The sequence had failures")
    case.step("final installed bytes and receipt", lambda: verify_files(case))


class CdpSocket:
    """A minimal RFC 6455 client for Chrome's loopback DevTools endpoint; no third-party code."""

    def __init__(self, port, path):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            (
                f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        reply = b""
        while b"\r\n\r\n" not in reply:
            chunk = self.sock.recv(4096)
            check(chunk, "The browser closed the DevTools handshake")
            reply += chunk
        head, _, self.buffer = reply.partition(b"\r\n\r\n")
        check(head.startswith(b"HTTP/1.1 101"), "The browser refused the DevTools upgrade")

    def _read(self, count):
        while len(self.buffer) < count:
            chunk = self.sock.recv(1 << 20)
            check(chunk, "The browser closed the DevTools connection")
            self.buffer += chunk
        data, self.buffer = self.buffer[:count], self.buffer[count:]
        return data

    def send(self, text):
        payload = text.encode()
        header = bytearray([0x81])
        if len(payload) < 126:
            header.append(0x80 | len(payload))
        elif len(payload) < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", len(payload))
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", len(payload))
        mask = os.urandom(4)
        self.sock.sendall(bytes(header) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv(self, timeout):
        self.sock.settimeout(timeout)
        message = b""
        while True:
            first, second = self._read(2)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read(8))[0]
            payload = self._read(length)
            opcode = first & 0x0F
            if opcode == 0x8:
                fail("The browser closed the DevTools connection")
            if opcode == 0x9:
                continue  # Chrome does not ping; answering is unnecessary for this bounded session.
            message += payload
            if first & 0x80:
                return message.decode()

    def close(self):
        self.sock.close()


class Browser:
    """A real headless Chrome with a private profile, driven with real mouse and key events over DevTools."""

    def __init__(self, case):
        self.case = case
        self.profile = case.root / "chrome-profile"
        self.shots = case.root / "shots"
        self.shots.mkdir(mode=0o700, exist_ok=True)
        self.sock = self.process = self.session = None
        self.next_id = 0
        self.seen = []
        chrome = case.ctx["chrome"]
        check(Path(chrome).is_file(), f"Chrome is not installed at {chrome}")
        env = {
            "HOME": str(case.root / "home"),
            "TMPDIR": str(case.root / "tmp"),
            "PATH": "/usr/bin:/bin",
            "LANG": case.env.get("LANG", "en_US.UTF-8"),
        }
        argv = [
            chrome,
            "--headless=new",
            "--remote-debugging-port=0",
            f"--user-data-dir={self.profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-sync",
            "--disable-default-apps",
            "--disable-breakpad",
            "--use-mock-keychain",
            "--password-store=basic",
            "--window-size=1280,900",
            *(
                ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
                if case.ctx.get("chrome_no_sandbox")
                else []
            ),
            "about:blank",
        ]
        self.process = case.ctx["Guarded"](argv, cwd=case.root, env=env)
        try:
            self._connect()
        except BaseException:
            self.close()
            raise

    def _connect(self):
        case = self.case
        deadline = time.monotonic() + 60
        active = self.profile / "DevToolsActivePort"
        while time.monotonic() < deadline and not active.is_file():
            self.process.pump(0.2)
            check(self.process.status is None, "Chrome exited before it opened its DevTools endpoint")
        check(active.is_file(), "Chrome did not open its DevTools endpoint")
        port, path = active.read_text().split()[:2]
        check(int(port) != LIVE_PORT, "Chrome's debugging port collided with the live app's port")
        self.sock = CdpSocket(int(port), path)
        target = self.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        self.session = self.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        self.call("Page.enable", session=True)
        self.call("Runtime.enable", session=True)
        self.version = self.call("Browser.getVersion")["product"]

    def call(self, method, params=None, *, session=False, timeout=60):
        self.next_id += 1
        message = {"id": self.next_id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = self.session
        self.sock.send(json.dumps(message))
        deadline = time.monotonic() + timeout
        while True:
            remain = deadline - time.monotonic()
            check(remain > 0, f"DevTools call {method} timed out")
            reply = json.loads(self.sock.recv(remain))
            if reply.get("id") == self.next_id:
                if "error" in reply:
                    fail(f"DevTools call {method} failed: {reply['error'].get('message')}")
                return reply.get("result", {})
            if reply.get("method") == "Runtime.exceptionThrown" or reply.get("method") == "Log.entryAdded":
                self.seen.append(reply["method"])

    def evaluate(self, expression):
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            session=True,
        )
        if "exceptionDetails" in result:
            fail(f"The page script failed: {result['exceptionDetails'].get('text')}")
        return result["result"].get("value")

    def goto(self, url):
        self.call("Page.navigate", {"url": url}, session=True)

    def wait(self, expression, what, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                value = self.evaluate(expression)
            except StepFailed:
                value = None  # the page is reloading
            if value:
                return value
            time.sleep(0.4)
        text = ""
        try:
            text = self.evaluate("document.body.innerText")[:400]
        except StepFailed:
            pass
        fail(f"Timed out waiting for {what}. Page text: {redact(text)!r}")
        return None

    @staticmethod
    def locate(selector, text, exact=True):
        return (
            "(()=>{const t=" + json.dumps(text) + ";const ex=" + json.dumps(exact) + ";"
            "const vis=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&"
            "getComputedStyle(e).visibility!=='hidden'};"
            "const name=e=>(e.getAttribute('aria-label')||e.textContent||e.title||'').trim();"
            "return [...document.querySelectorAll(" + json.dumps(selector) + ")]"
            ".filter(e=>vis(e)&&(ex==='suffix'?name(e).endsWith(t):ex?name(e)===t:name(e).includes(t)))[0]||null})()"
        )

    def present(self, selector, text, exact=True):
        return self.evaluate(self.exists(selector, text, exact))

    def exists(self, selector, text, exact=True):
        return "!!" + self.locate(selector, text, exact)

    def click(self, selector, text, exact=True, timeout=60):
        locate = self.locate(selector, text, exact)
        point = self.wait(
            f"(()=>{{const e={locate};if(!e)return null;e.scrollIntoView({{block:'center'}});"
            "const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()",
            f"{selector} {text!r}",
            timeout,
        )
        base = {"x": point["x"], "y": point["y"], "button": "left", "clickCount": 1}
        self.call("Input.dispatchMouseEvent", {**base, "type": "mouseMoved"}, session=True)
        self.call("Input.dispatchMouseEvent", {**base, "type": "mousePressed"}, session=True)
        self.call("Input.dispatchMouseEvent", {**base, "type": "mouseReleased"}, session=True)

    def type_into(self, selector, text):
        self.wait(
            f"(()=>{{const e=document.querySelector({json.dumps(selector)});if(!e)return false;e.focus();"
            "e.select&&e.select();return document.activeElement===e})()",
            f"{selector} focus",
            30,
        )
        self.call("Input.insertText", {"text": text}, session=True)

    def press(self, key, code, virtual):
        base = {"key": key, "code": code, "windowsVirtualKeyCode": virtual, "text": "\r" if key == "Enter" else ""}
        self.call("Input.dispatchKeyEvent", {**base, "type": "keyDown"}, session=True)
        self.call("Input.dispatchKeyEvent", {**base, "type": "keyUp"}, session=True)

    def shot(self, name):
        data = self.call("Page.captureScreenshot", {"format": "png"}, session=True)["data"]
        path = self.shots / f"{name}.png"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(base64.b64decode(data))
        return path.name

    def close(self):
        if self.sock:
            self.sock.close()
        if self.process:
            process, self.process = self.process, None
            process.stop()


# --- Lifecycle cases (6-12) -------------------------------------------------------------------------------------


def bootstrap(case, name, *, retained=False):
    """Install the app the way this run's --bootstrap says, and prove it; the override env never outlives the call."""
    boot = case.ctx["bootstrap"]
    case.ctx.update(profile=boot["profile"], accepted=boot["accepted"])
    case.expect_version, case.core_wheel_override = boot["version"], boot.get("core_wheel")
    case.plugins_expected = None
    case.env.update(boot["env"])
    try:
        app_cycle(case, name, boot["command"], retained=retained)
    finally:
        for key in boot["env"]:
            case.env.pop(key, None)
    case.notes["bootstrap"] = boot["mode"]


def hz(case, *args, label):
    return case.json_command(*args, label=label)


def installed_interpreter(case):
    python = (case.prefix / "bin/python").resolve(strict=True)
    check(python.is_relative_to((case.root / "uv-python").resolve(strict=True)), "Not the case's managed Python")
    return str(python)


def plugin_map(case, label):
    return {p["distribution"]: p for p in hz(case, "system", "plugin", "list", label=label)}


def expect_plugins(case, **changes):
    """Record what the inventory must show next: name=None removes it, name=dict sets/updates it."""
    if case.plugins_expected is None:
        wanted = {a["name"]: a["version"] for a in case.ctx["profile"]["artifacts"] if a["name"] != "heinzel"}
        case.plugins_expected = {n: {"enabled": True, "version": v} for n, v in wanted.items()}
    for name, value in changes.items():
        name = name.replace("_", "-")
        if value is None:
            case.plugins_expected.pop(name, None)
        else:
            case.plugins_expected[name] = {**case.plugins_expected.get(name, {"enabled": True}), **value}


def verify_state(case, label, *, retained=False):
    """Everything that does not need a running app: checkpoint, CLI identity, inventory, nothing enabled."""
    result = verify_cli(case, retained=retained)
    result["interpreter"] = installed_interpreter(case)
    return {label: result["cli_plugins"], "checkpoint": result["startup_checkpoint"]}


def candidate_wheel_of(case):
    wheel = case.ctx.get("candidate")
    check(wheel, "This case needs --candidate-wheel")
    return wheel


def snapshot_files(root):
    return {str(p): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file() and not p.name.startswith(".")}


def ui_app(case, label):
    """Start the installed app the way a user does after installation; returns its foreground session."""
    return case.foreground([str(case.bin / "hz"), "ui"], label)


def run_update(case, label):
    """The update the user performs: Core from PyPI when a newer release is published, else the candidate wheel."""
    target = case.ctx.get("update_target")
    check(target, "No update target: neither a newer published release nor --candidate-wheel")
    args = ["system", "update", "--json"] + (
        ["--source", str(target["wheel"])] if target["mode"] == "candidate" else []
    )
    _, out, _ = case.run([str(case.bin / "hz"), *args], label, PHASE_DEADLINE * 2)
    result = json.loads(out)
    check(result["updated"] is True, f"The update reported updated={result['updated']}: {result.get('message')}")
    check(result["after"] == target["version"], f"The update installed {result['after']}, expected {target['version']}")
    return result


def post_update_profile(case):
    """What is installed after the update, as a profile: the target Core plus the old composition's plugins."""
    old, target = case.ctx["from_app"], case.ctx["update_target"]
    core = {
        "name": "heinzel",
        "version": target["version"],
        "filename": target["wheel"].name,
        "sha256": file_sha256(target["wheel"]),
        "url": str(target["wheel"]),
    }
    plugins = [a for a in old["profile"]["artifacts"] if a["name"] != "heinzel"]
    return {"format": 2, "core_version": target["version"], "sdk": 1, "artifacts": [core, *plugins]}


def case_update(case):
    """Published 0.9.8 -> the new release: Core replaced, interpreter, configuration, plugins and choices kept."""
    case.provide_uv()
    old = case.ctx.get("from_app")
    check(old, "The starting app could not be bound")
    target = case.ctx.get("update_target")
    if not target:
        raise Skipped("No release newer than the starting app is published and no --candidate-wheel was given")
    case.ctx.update(profile=old["profile"], accepted=old["accepted"])
    case.expect_version, case.core_wheel_override = old["version"], None
    case.env["HZ_APP_PROFILE"] = old["url"]
    try:
        app_cycle(case, f"published {old['version']} install", ["uvx", f"heinzel@{old['version']}"])
    finally:
        case.env.pop("HZ_APP_PROFILE", None)

    def prepare():
        hz(case, "system", "plugin", "add", "heinzel-example", "--confirm", label="user-plugin-add")
        hz(case, "system", "plugin", "disable", "heinzel-resource", label="user-disable")
        hz(case, "system", "config", "set", "clock.zones", '["Europe/Berlin","Asia/Tokyo"]', label="user-config")
        expect_plugins(case, heinzel_example={"enabled": True}, heinzel_resource={"enabled": False})
        case.notes["python_before"] = installed_interpreter(case)
        case.notes["config_before"] = snapshot_files(case.root / "config_home")
        check(case.notes["config_before"], "No user configuration exists to retain")
        return {"user_plugin": "heinzel-example", "disabled_default": "heinzel-resource", "configured": True}

    case.step("user state before the update: extra plugin, disabled default, configuration", prepare)
    case.step("state before the update verified", lambda: verify_state(case, "before"))
    result = case.step("the update the user runs", lambda: _summarize(run_update(case, "hz-system-update")))

    def after():
        case.expect_version, case.core_wheel_override = target["version"], target["wheel"]
        profile = post_update_profile(case)
        evidence = {
            "core_files": verify_files(
                case, profile=profile, accepted=target["wheel"].parent, receipt_profile=old["profile"], plugins=False
            )["wheel_files_identical"]
        }
        receipt = json.loads((case.root / "data_dir/standard-app.json").read_bytes())
        evidence["receipt_core_version_after_update"] = receipt["core_version"]
        evidence["receipt_matches_installed_core"] = receipt["core_version"] == target["version"]
        check(installed_interpreter(case) == case.notes["python_before"], "The update changed the managed interpreter")
        check(snapshot_files(case.root / "config_home") == case.notes["config_before"], "Configuration changed")
        if target["mode"] == "published":
            for entry in case.plugins_expected.values():
                entry["version"] = None  # PyPI may be ahead of the app profile; versions are recorded, not compared
        evidence.update(verify_state(case, "after"))
        evidence["plugin_versions_after"] = {p: v["version"] for p, v in plugin_map(case, "versions-after").items()}
        return evidence

    case.step("installed Core bytes, interpreter, configuration and inventory after the update", after)

    def serve():
        session = ui_app(case, "updated-ui")
        try:
            return verify_http(case, session)
        finally:
            session.stop()

    case.step("updated app serves the target Core's identity and frontend", serve)

    def offline():
        env = {"UV_OFFLINE": "1", "HZ_APP_PROFILE": str(case.root / "does-not-exist.json")}
        saved = dict(case.env)
        case.env.update(env)
        try:
            _, out, _ = case.run([str(case.bin / "heinzel"), "system", "info", "--json"], "offline-continuity", 120)
        finally:
            case.env.clear()
            case.env.update(saved)
        return {"offline_continuity": json.loads(out)["version"] == target["version"]}

    case.step("updated app runs offline without any profile", offline)
    case.step("a second update is a no-op", lambda: _second_update(case, target))


def _summarize(result):
    return {
        "before": result["before"],
        "after": result["after"],
        "plugins": {p["distribution"]: p["status"] for p in result.get("plugins", [])},
    }


def _second_update(case, target):
    _, out, _ = case.run(
        [str(case.bin / "hz"), "system", "update", "--json"], "hz-system-update-again", PHASE_DEADLINE * 2
    )
    result = json.loads(out)
    check(result["updated"] is False, f"A repeated update changed something: {result.get('message')}")
    check(result["after"] == target["version"], "A repeated update changed the Core version")
    return {"repeat": result.get("message")}


def case_plugin_cli(case):
    case.provide_uv()
    bootstrap(case, "install")

    def preview():
        before = plugin_map(case, "plugins-before")
        answer = hz(case, "system", "plugin", "add", "heinzel-example", label="example-preview")
        check(answer["done"] is False and answer["candidate"]["distribution"] == "heinzel-example", "No preview")
        check(plugin_map(case, "plugins-after-preview").keys() == before.keys(), "A preview changed the inventory")
        return {"version": answer["candidate"]["version"], "publisher": answer["candidate"]["publisher"]}

    def add(spec, label, version):
        answer = hz(case, "system", "plugin", "add", spec, "--confirm", label=label)
        check(answer["done"] is True, f"{spec} was not installed: {answer.get('error')}")
        name = answer["distribution"]
        expect_plugins(case, **{name: {"enabled": True, "version": version}})
        check(plugin_map(case, label + "-list")[name]["version"] == version, f"{name} is not at {version}")
        return {"installed": name, "version": version}

    case.step("preview installs nothing", preview)
    case.step("install a plugin (CLI)", lambda: add("heinzel-example", "example-add", "0.4.0"))
    case.step("inventory and checkpoint after install", lambda: verify_state(case, "installed"))
    case.step("pin an older plugin version (CLI)", lambda: add("heinzel-clock==0.9.2", "clock-older", "0.9.2"))
    case.step(
        "update the plugin to the newest (CLI re-add replaces)", lambda: add("heinzel-clock", "clock-update", "0.9.3")
    )

    def toggle():
        hz(case, "system", "plugin", "disable", "heinzel-resource", label="resource-disable")
        expect_plugins(case, heinzel_resource={"enabled": False})
        check(plugin_map(case, "after-disable")["heinzel-resource"]["enabled"] is False, "Disable had no effect")
        hz(case, "system", "plugin", "enable", "heinzel-resource", label="resource-enable")
        expect_plugins(case, heinzel_resource={"enabled": True})
        return {"toggled": True}

    case.step("disable and enable a default plugin", toggle)

    def remove():
        hz(case, "system", "plugin", "remove", "heinzel-example", label="example-remove")
        expect_plugins(case, heinzel_example=None)
        check("heinzel-example" not in plugin_map(case, "after-remove"), "The plugin is still installed")
        receipt = (case.prefix / "uv-receipt.toml").read_text()
        check("heinzel-example" not in receipt, "The tool receipt still names the removed plugin")
        history = [json.loads(x)["action"] for x in (case.root / "data_dir/plugins.jsonl").read_text().splitlines()]
        return {"history": history}

    case.step("remove the plugin (CLI)", remove)
    case.step("inventory and checkpoint after removal", lambda: verify_state(case, "removed"))


UI_NAV = (
    ("button", "Profile", True),
    ("[role=menuitem]", "Settings", True),
    ("button", "Plugins", True),
)


def ui_open_plugins(browser, case):
    """Profile menu -> Settings -> Plugins, with clicks only, after dismissing a first-run setup if it appears."""
    time.sleep(3)  # a first-run setup dialog may open late; it is what a user dismisses first
    if browser.present("button", "Close setup"):
        browser.click("button", "Close setup")
        browser.wait("!document.querySelector('[role=dialog]')", "the setup dialog to close", 30)
    browser.click("button", "Profile", "suffix")
    browser.click("[role=menuitem]", "Settings", False)
    browser.click("button", "Plugins")
    browser.wait("document.body.innerText.includes('Installed on this machine')", "the installed plugin list", 60)


def ui_apply(browser, what):
    """The two-step confirmation the page asks for: Restart and apply, then Restart and apply Plugins.

    The caller waits for the end state it expects; the server restarts itself and the page reloads.
    """
    browser.click("button", "Restart and apply")
    browser.wait(browser.exists("button", "Restart and apply Plugins"), f"the restart confirmation of {what}", 30)
    browser.click("button", "Restart and apply Plugins")


def ui_add(browser, spec, shot, expect_detail):
    if not browser.evaluate("!!document.querySelector(\"input[placeholder*='git+https']\")"):
        browser.click("button", "Add")
    browser.type_into("input[placeholder*='git+https']", spec)
    browser.press("Enter", "Enter", 13)
    browser.wait("document.body.innerText.includes('Install this plugin?')", f"the preview of {spec}", 90)
    text = browser.evaluate("document.body.innerText")
    for fragment in expect_detail:
        check(fragment in text, f"The preview of {spec} lacks {fragment!r}")
    browser.shot(shot)
    return text


def row_has(name, version):
    """A page expression: the plugin's row (the Remove button's nearest ancestor with text) shows `v<version>`."""
    return (
        "(()=>{let e=document.querySelector(" + json.dumps(f"button[aria-label='Remove {name}']") + ");"
        "for(let i=0;i<6&&e;i++,e=e.parentElement){if(e.innerText&&e.innerText.includes('v"
        + version
        + "'))return true}"
        "return false})()"
    )


def case_plugin_ui(case):
    case.provide_uv()
    bootstrap(case, "install")
    holder = {}

    def launch():
        session = ui_app(case, "plugin-ui-app")
        holder["session"], holder["pid"] = session, session.process.process.pid
        browser = Browser(case)
        holder["browser"] = browser
        browser.goto(f"{session.base}/#token={session.token}")
        browser.wait(
            "document.body.innerText.length>50&&" + browser.exists("button", "Profile", "suffix"),
            "the app to render",
            120,
        )
        ui_open_plugins(browser, case)
        expected = sorted(
            case.plugins_expected or {a["name"] for a in case.ctx["profile"]["artifacts"] if a["name"] != "heinzel"}
        )
        listed = "[...document.querySelectorAll('button[aria-label^=\"Remove \"]')].map(b=>b.getAttribute('aria-label').slice(7))"
        browser.wait(f"{listed}.length>={len(expected)}", "the installed plugins to be listed", 60)
        names = browser.evaluate(listed)
        check(sorted(names) == expected, f"The Plugins page lists {names}, not the installed {expected}")
        return {"browser": browser.version, "listed": sorted(names), "screenshot": browser.shot("01-installed")}

    def server_state(label):
        """The restarted server is the same process image: record its command line, once, for isolation."""
        out = subprocess.run(
            ["/bin/ps", "-o", "command=", "-p", str(holder["pid"])],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        ).stdout.strip()
        isolated = " -I " in f" {out} "
        case.notes.setdefault("server_isolated_after_restart", []).append({label: isolated})
        return isolated

    def install():
        browser = holder["browser"]
        text = ui_add(browser, "heinzel-example", "02-install-preview", ["heinzel-example", "0.4.0"])
        ui_apply(browser, "the install")
        browser.wait(row_has("heinzel-example", "0.4.0"), "the new plugin's row at v0.4.0", 360)
        expect_plugins(case, heinzel_example={"enabled": True, "version": "0.4.0"})
        cli = plugin_map(case, "plugins-after-ui-install")
        check(cli["heinzel-example"]["version"] == "0.4.0", "The CLI does not see the UI-installed plugin")
        return {
            "screenshot": browser.shot("03-installed"),
            "isolated_server": server_state("after-install"),
            "preview_has_details": "Publisher" in text,
        }

    def update():
        browser = holder["browser"]
        results = {}
        for spec, expected, shot in (("heinzel-clock==0.9.2", "0.9.2", "04"), ("heinzel-clock", "0.9.3", "05")):
            text = ui_add(browser, spec, f"{shot}-preview", ["heinzel-clock", expected])
            check("Replaces" in text, f"The preview of {spec} does not say it replaces a version")
            ui_apply(browser, f"the {spec} change")
            browser.wait(row_has("heinzel-clock", expected), f"Clock at v{expected}", 360)
            expect_plugins(case, heinzel_clock={"enabled": True, "version": expected})
            check(plugin_map(case, f"clock-{expected}")["heinzel-clock"]["version"] == expected, "CLI disagrees")
            results[spec] = {"screenshot": browser.shot(f"{shot}-done")}
        return results

    def remove():
        browser = holder["browser"]
        browser.click("button", "Remove heinzel-example")
        browser.wait("document.body.innerText.includes('Remove heinzel-example?')", "the removal question", 30)
        browser.shot("06-remove-ask")
        ui_apply(browser, "the removal")
        browser.wait("!document.querySelector(\"button[aria-label='Remove heinzel-example']\")", "the row to go", 360)
        expect_plugins(case, heinzel_example=None)
        check("heinzel-example" not in plugin_map(case, "plugins-after-ui-remove"), "The CLI still has the plugin")
        return {"screenshot": browser.shot("07-removed"), "isolated_server": server_state("after-remove")}

    def identity():
        return verify_http(case, holder["session"])

    def teardown():
        stopped = None
        try:
            browser = holder.get("browser")
            if browser:
                holder["errors"] = len(browser.seen)
                browser.close()
        finally:
            session = holder.get("session")
            if session:
                stopped = session.stop()
        return stopped

    try:
        case.step("real browser opens the app and Settings -> Plugins lists the install", launch)
        case.step("install a plugin through the page", install)
        case.step("update a plugin through the page (replace)", update)
        case.step("remove a plugin through the page", remove)
        case.step("the restarted app still serves the right identity and assets", identity)
    finally:
        case.step("stop the browser and the owned app", teardown)
    case.step("inventory and checkpoint after the page changes", lambda: verify_state(case, "after-ui"))


def case_retention(case):
    """A user uninstalls and later reinstalls: what a reinstall keeps, drops or brings back; then a full wipe."""
    case.provide_uv()
    bootstrap(case, "install")

    def prepare():
        hz(case, "system", "plugin", "add", "heinzel-example", "--confirm", label="example-add")
        hz(case, "system", "plugin", "disable", "heinzel-resource", label="resource-disable")
        hz(case, "system", "config", "set", "clock.zones", '["Europe/Berlin","Asia/Tokyo"]', label="zones")
        case.notes["kept"] = snapshot_files(case.root / "config_home")
        case.notes["history"] = (case.root / "data_dir/plugins.jsonl").read_bytes()
        return {"files": len(case.notes["kept"])}

    case.step("user state: extra plugin, disabled default, configuration", prepare)
    case.step("uv tool uninstall removes prefix and shims", lambda: uninstall(case, "uv-tool-uninstall"))

    def retained():
        check(snapshot_files(case.root / "config_home") == case.notes["kept"], "Configuration changed on uninstall")
        check((case.root / "data_dir/plugins.jsonl").read_bytes() == case.notes["history"], "History changed")
        return {"config_and_history_retained": True}

    case.step("configuration and plugin history survive the uninstall", retained)
    expect_plugins(case, heinzel_resource={"enabled": False})
    case.plugins_expected["heinzel-example"] = {"enabled": True, "optional": True}  # observed below, not assumed
    case.step("reinstall", lambda: bootstrap_keep(case, "reinstall"))

    def after_reinstall():
        inventory = plugin_map(case, "plugins-after-reinstall")
        check(inventory["heinzel-resource"]["enabled"] is False, "The disabled default came back enabled")
        check(snapshot_files(case.root / "config_home") == case.notes["kept"], "Configuration changed on reinstall")
        case.notes["example_back_after_reinstall"] = "heinzel-example" in inventory
        return {
            "user_added_plugin_present_after_reinstall": "heinzel-example" in inventory,
            "disabled_choice_kept": True,
            "configuration_kept": True,
        }

    case.step("reinstall keeps configuration and the disabled choice", after_reinstall)
    case.step("uninstall again", lambda: uninstall(case, "uv-tool-uninstall-2"))

    def wipe():
        for name in ("config_home", "data_dir", "state_dir"):
            shutil.rmtree(case.root / name)
        return {"wiped": ["config_home", "data_dir", "state_dir"]}

    case.step("the user deletes configuration, data and state", wipe)
    case.plugins_expected = None
    case.step("reinstall after a full wipe is a fresh install", lambda: bootstrap_keep(case, "fresh after wipe"))
    case.step("fresh state verified", lambda: verify_state(case, "fresh"))


def bootstrap_keep(case, name):
    """A reinstall in an existing case: like bootstrap, but the existing expectations stay."""
    expected = case.plugins_expected
    boot = case.ctx["bootstrap"]
    case.env.update(boot["env"])
    try:
        app_cycle_keep(case, name, boot["command"], expected)
    finally:
        for key in boot["env"]:
            case.env.pop(key, None)
    return {"reinstalled": True}


def app_cycle_keep(case, name, command, expected):
    """app_cycle with an inventory expectation; steps are recorded exactly as app_cycle records them."""
    case.plugins_expected = expected
    holder = case.step(f"{name}: public command reaches foreground app", lambda: _start(case, name, command))["session"]
    try:
        case.step(f"{name}: installed bytes, receipt, managed Python", lambda: verify_files(case))
        case.step(f"{name}: HTTP identity, plugins, frontend assets", lambda: verify_http(case, holder))
    finally:
        case.step(f"{name}: stop owned foreground app", holder.stop)
    case.step(f"{name}: CLI identity, checkpoint, nothing enabled", lambda: verify_cli(case))


def case_stopped_unit_update(case):
    """An installed but stopped daemon unit (registration simulated) while the user updates."""
    case.provide_uv()
    bootstrap(case, "install")
    if sys.platform != "darwin":
        raise Unprovable(
            "The stopped-unit steps need the quiescence proof, which reads the systemd user manager's private socket; "
            "a hosted Linux runner has none, and nothing is registered to obtain one."
        )
    plist = case.root / "units" / f"{LABEL}.plist"
    case.step("trusted unit with intercepted registration", lambda: install_fixture(case))
    before = snapshot(case, retained_paths_no_config(case))
    target = case.ctx.get("update_target")

    def update():
        if not target:
            raise Skipped("No update target to run with a retained unit")
        installed = case.expect_version
        if target["version"] == installed:
            raise Skipped("The installed Core already is the target")
        return _summarize(run_update(case, "hz-system-update-with-unit"))

    case.step("the user's update with a stopped unit present", update)

    def unit_kept():
        check(snapshot(case, retained_paths_no_config(case)) == before, "The retained unit or marker changed")
        check(plist.is_file(), "The unit disappeared")
        daemon = hz(case, "system", "daemon", "status", label="daemon-status-after")
        check(not daemon.get("loaded"), "The daemon reports loaded")
        return {"unit_bytes_unchanged": True, "installed": bool(daemon.get("installed"))}

    case.step("the unit and its markers are unchanged and not loaded", unit_kept)


def retained_paths_no_config(case):
    daemon = case.root / "state_dir/daemon"
    return [case.root / "units" / f"{LABEL}.plist", daemon / "install.json", daemon / "paused"]


def case_active_daemon(case):
    """Record why an active-daemon update cannot be run here, from the shipped code, and what would be needed."""
    facts = {}
    for label, wheel in (("published", case.ctx["accepted"]), ("candidate", case.ctx.get("candidate_dir"))):
        if wheel is None:
            continue
        core = sorted(Path(wheel).glob("heinzel-*.whl"))
        if not core:
            continue
        with zipfile.ZipFile(core[0]) as archive:
            units = archive.read("heinzel/modules/system/daemon/_units.py").decode()
        match = re.search(r'^LABEL\s*=\s*"([^"]+)"', units, re.MULTILINE)
        facts[label] = {
            "label_is_module_constant": bool(match),
            "label_equals_real_label": bool(match) and match.group(1) == LABEL,
            "reads_environment_for_label": "environ" in units,
        }

    def record():
        check(facts, "No Core wheel was available to inspect")
        return facts

    case.step("the daemon label is a fixed constant of the shipped Core", record)
    raise Unprovable(
        "service-manager identities cannot be made private: the shipped Core hard-codes the daemon label and unit "
        "names, and its launchctl or systemctl commands target the user's own manager, so any real activation would "
        "register, replace or stop the real daemon's identity. Required: a Core setting or variable that renames the "
        "label (a small product change), or a separate user or VM with its own manager, or a run on a machine with a "
        "real daemon."
    )


def case_private_plugin(case):
    """A plugin from a private repository cannot be installed without a sign-in; this harness holds no credentials."""
    raise Unprovable(
        "A plugin from a private repository is installed with a sign-in, and this harness holds no credentials by "
        "design, so there is nothing to install and nothing to prove here. A credentialed run elsewhere owns that "
        "evidence."
    )


def assert_final_install(case, where):
    """The default path installed a final version and a final profile, never an edge or pre-release one."""
    receipt = json.loads((case.root / "data_dir/standard-app.json").read_bytes())
    versions = {a["name"]: a["version"] for a in receipt.get("artifacts", [])}
    check(versions, f"{where}: the receipt names no distributions")
    check(
        is_accepted_core(receipt.get("core_version")),
        f"{where}: receipt Core {receipt.get('core_version')} is not final",
    )
    for name, version in versions.items():
        check(is_final_version(version), f"{where}: {name} {version} is not a final version")
    info = case.json_command("system", "info", label="hz-system-info-final")
    check(is_accepted_core(info["version"]), f"{where}: the installed Core {info['version']} is not a final version")
    check(info["version"] == receipt["core_version"], f"{where}: the receipt and the installed Core differ")
    return {"core": info["version"], "distributions": versions}


def case_default_is_stable(case):
    """The default never selects edge: literal `uvx heinzel@latest` and the curl installer's app discovery."""
    skip_for_candidate(case, "This case proves what the public commands select.")
    case.provide_uv()

    def discovery():
        designated = latest_app_version()
        profile = case.ctx["profile"]
        check(is_final_version(designated), f"The designated latest app {designated} is not a final version")
        check(
            is_final_version(profile["core_version"]), f"The profile selects a non-final Core {profile['core_version']}"
        )
        check(
            all(is_final_version(a["version"]) for a in profile["artifacts"]),
            "The profile names a non-final distribution",
        )
        return {"designated_app": designated, "profile_core": profile["core_version"]}

    case.step("the latest app designation and its profile are final versions", discovery)
    app_cycle(case, "default uvx", LITERAL_UVX)
    case.step("default uvx installed a final Core and profile", lambda: assert_final_install(case, "uvx"))
    case.step("uv tool uninstall removes prefix and shims", lambda: uninstall(case, "uv-tool-uninstall-default"))
    app_cycle(case, "default curl", shell_literal(LITERAL_CURL))
    case.step("default curl installed a final Core and profile", lambda: assert_final_install(case, "curl"))


RUNNERS = {
    "1": case_fresh_curl,
    "2": case_fresh_uvx,
    "3": case_retained,
    "4": case_reinstall,
    "5": case_sequence,
    "6": case_update,
    "7": case_plugin_cli,
    "8": case_plugin_ui,
    "9": case_retention,
    "10": case_stopped_unit_update,
    "11": case_active_daemon,
    "12": case_private_plugin,
    "13": case_default_is_stable,
}


def run_case(ident, parent, ctx):
    case = Case(ident, parent, ctx)
    try:
        RUNNERS[ident](case)
        case.status = "FAIL" if case.hits else "PASS"
    except Refused as error:
        case.status = "REFUSED"
        case.first_failure = {"refused": str(error)}
    except Skipped as error:
        case.status = "SKIPPED"
        case.first_failure = {"skipped": str(error)}
    except Unprovable as error:
        case.status = "UNPROVABLE"
        case.first_failure = {"unprovable": str(error)}
    except StepFailed:
        case.status = "FAIL"
    except Exception as error:  # A harness defect must still leave a truthful record.
        case.status = "FAIL"
        message = redact(f"harness {type(error).__name__}: {error}")[:500]
        case.first_failure = case.first_failure or {"step": "harness", "failure": message}
    return (
        {
            "name": case.name,
            "status": case.status,
            "root": str(case.root),
            "first_failure": case.first_failure,
            "forbidden_error_hits": case.hits,
            "notes": {
                k: v
                for k, v in case.notes.items()
                if isinstance(v, (str, bool, int, list, dict)) and k not in ("config_before", "kept", "history")
            },
            "steps": case.steps,
            "seconds": round(time.monotonic() - case.started, 1),
        },
        case.secrets,
        case.root,
    )


def verdict(args, cases):
    """The honest label for a run: a candidate or diagnostic run is never a pass of the literal public path."""
    states = [c["status"] for c in cases.values()]
    if args.candidate_wheel_url:
        if "FAIL" in states:
            return CANDIDATE_VERDICTS["FAIL"]
        if "REFUSED" in states:
            return "REFUSED"
        if len(states) != len(CASES):
            return CANDIDATE_VERDICTS["PARTIAL"]
        return CANDIDATE_VERDICTS["EXCEPT" if any(s in OLD_STATUSES for s in states) else "OK"]
    if args.diagnostic_explicit_profile or args.bootstrap != "literal" or args.candidate_wheel:
        return "NOT_ACCEPTANCE_DIAGNOSTIC"
    if "FAIL" in states:
        return "REJECTED"
    if "REFUSED" in states:
        return "REFUSED"
    if len(states) != len(CASES):
        return "PARTIAL_PASS"
    return "ACCEPTED_EXCEPT_UNPROVABLE" if any(s in OLD_STATUSES for s in states) else "ACCEPTED"


def exit_code(label):
    """0 only for a passing verdict. REJECTED, PREFLIGHT_FAILED and REFUSED, and anything unknown, fail the job."""
    return {"PREFLIGHT_FAILED": 2, "REFUSED": 3}.get(label, 0 if label in PASSING else 1)


def write_result(path, result, secrets):
    text = json.dumps(result, indent=2, sort_keys=False) + "\n"
    for secret in secrets:
        if secret in text:
            text = text.replace(secret, "<redacted>")
            result.setdefault("harness_warnings", []).append("A session token reached the result and was removed.")
    partial = path.with_suffix(".partial")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(text)
    partial.replace(path)


def copy_result(args, path):
    """Copy the finished result.json to --out (never overwriting), also after a failed preflight."""
    if not args.out:
        return
    args.out.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(path.read_text())


def secrets_in_logs(root, secrets):
    count = 0
    for log in root.glob("logs/*.log"):
        content = log.read_text(errors="replace")
        count += any(secret in content for secret in secrets)
    return count


def default_root_parent():
    """A private proof root's parent: /private/tmp on macOS, the runner's or system temp directory elsewhere."""
    if sys.platform == "darwin" and Path("/private/tmp").is_dir():
        return Path("/private/tmp")
    return Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir())


def default_chrome():
    if sys.platform == "darwin":
        return CHROME
    return next(
        (found for name in CHROME_LINUX if (found := shutil.which(name, path="/usr/bin:/bin"))), CHROME_LINUX[0]
    )


def ci_record():
    """Where a hosted run came from, for the receipt; nothing secret is read."""
    names = {
        "repository": "GITHUB_REPOSITORY",
        "sha": "GITHUB_SHA",
        "run_id": "GITHUB_RUN_ID",
        "run_attempt": "GITHUB_RUN_ATTEMPT",
        "workflow": "GITHUB_WORKFLOW",
        "event": "GITHUB_EVENT_NAME",
        "runner_os": "RUNNER_OS",
        "runner_arch": "RUNNER_ARCH",
        "image_version": "ImageVersion",
    }
    return {key: os.environ[name] for key, name in names.items() if os.environ.get(name)}


def release_key(version):
    return tuple(int(part) for part in version.split("."))


def bind_old_app(version, dest):
    """Bind the earlier published app (the update's starting point) to its release identity; executes nothing."""
    url = f"{COLLECTION}/releases/download/app-v{version}/standard.json"
    raw = fetch(url, 1024 * 1024)
    profile = json.loads(raw)
    check(profile.get("format") == 2 and profile.get("core_version") == version, f"app-v{version} is not format 2")
    dest.mkdir()
    for artifact in profile["artifacts"]:
        check(
            artifact["url"].startswith(f"{COLLECTION}/releases/download/app-v{version}/"), "Asset outside its release"
        )
        data = fetch(artifact["url"])
        check(sha256(data) == artifact["sha256"], f"{artifact['name']}: app-v{version} asset fails its profile hash")
        (dest / artifact["filename"]).write_bytes(data)
        check(wheel_version(dest / artifact["filename"], artifact["name"]) == artifact["version"], "Version mismatch")
    pypi = json.loads(fetch(f"https://pypi.org/pypi/heinzel/{version}/json", 8 * 1024 * 1024))
    wheels = [u["digests"]["sha256"] for u in pypi["urls"] if u["packagetype"] == "bdist_wheel" and not u.get("yanked")]
    core = next(a for a in profile["artifacts"] if a["name"] == "heinzel")
    check(wheels == [core["sha256"]], f"app-v{version}'s Core differs from PyPI's {version} wheel")
    return {"profile": profile, "accepted": dest, "version": version, "url": url, "profile_sha256": sha256(raw)}


def bind_candidate(path, parent):
    """Copy the candidate wheel into the proof root and bind its identity. It is never imported here."""
    source = Path(path).resolve(strict=True)
    check(source.is_file() and source.suffix == ".whl", "--candidate-wheel must be a wheel file")
    check(source.stat().st_size < MAX_DOWNLOAD, "The candidate wheel is too large")
    directory = parent / "candidate"
    directory.mkdir(mode=0o700)
    wheel = directory / source.name
    shutil.copyfile(source, wheel)
    version = wheel_version(wheel, "heinzel")
    return {"wheel": wheel, "dir": directory, "version": version, "sha256": file_sha256(wheel)}


def candidate_app(candidate, profile, accepted, parent):
    """A local standard-app profile whose Core is the candidate and whose plugins are the published app's."""
    directory = parent / "accepted-candidate"
    directory.mkdir(mode=0o700)
    artifacts = []
    for artifact in profile["artifacts"]:
        if artifact["name"] == "heinzel":
            continue
        shutil.copyfile(accepted / artifact["filename"], directory / artifact["filename"])
        artifacts.append({**artifact, "url": str(directory / artifact["filename"])})
    shutil.copyfile(candidate["wheel"], directory / candidate["wheel"].name)
    core = {
        "name": "heinzel",
        "version": candidate["version"],
        "filename": candidate["wheel"].name,
        "sha256": candidate["sha256"],
        "url": str(directory / candidate["wheel"].name),
    }
    local = {"format": 2, "core_version": candidate["version"], "sdk": 1, "artifacts": [core, *artifacts]}
    path = directory / "standard.json"
    path.write_text(json.dumps(local, indent=2) + "\n")
    return local, directory, path


def bind_release_candidate(args, parent):
    """Bind a held release's wheel (URL plus sha256) and the app profile that selects it. Executes nothing.

    Only final and edge versions on this project's release pages are accepted. The wheel and every profile artifact are
    downloaded, hashed and bound before any case runs; PyPI is not consulted, because the bytes are not published yet.
    """
    version, app = args.expect_version, args.expect_app_version
    check(version == app and CANDIDATE_VERSION.fullmatch(version), "A candidate names one final or edge version")
    wheel_url, profile_url = args.candidate_wheel_url, args.candidate_profile_url
    wheel_match = RELEASE_WHEEL_URL.fullmatch(wheel_url or "")
    check(wheel_match, "--candidate-wheel-url must be this project's final release wheel")
    check(wheel_match["version"] == version, "The candidate wheel's version differs from --expect-version")
    profile_match = RELEASE_PROFILE_URL.fullmatch(profile_url or "")
    check(profile_match, "--candidate-profile-url must be a final app release's standard.json")
    check(profile_match["version"] == app, "The candidate profile's app version differs from --expect-app-version")
    wanted = args.candidate_wheel_sha256 or ""
    check(re.fullmatch(r"[0-9a-f]{64}", wanted), "--candidate-wheel-sha256 must be a lowercase SHA-256")

    directory = parent / "candidate"
    directory.mkdir(mode=0o700)
    wheel = directory / wheel_url.rsplit("/", 1)[1]
    data = fetch(wheel_url)
    check(sha256(data) == wanted, "The release wheel fails the supplied SHA-256")
    wheel.write_bytes(data)
    check(
        wheel_version(wheel, "heinzel") == version, "The release wheel's metadata version differs from --expect-version"
    )

    profile_bytes = fetch(profile_url, 1024 * 1024)
    profile = json.loads(profile_bytes)
    check(profile.get("format") == 2, "The profile is not format 2")
    check(profile.get("core_version") == version, "The profile selects a different Core")
    core = [a for a in profile["artifacts"] if a["name"] == "heinzel"]
    check(len(core) == 1 and core[0]["sha256"] == wanted, "The profile's Core differs from the supplied wheel hash")
    if args.accept_profile_sha256:
        check(sha256(profile_bytes) == args.accept_profile_sha256, "Profile differs from the accepted hash")
    accepted = parent / "accepted"
    accepted.mkdir()
    (accepted / "standard.json").write_bytes(profile_bytes)
    report = {
        "expect_version": version,
        "expect_app_version": app,
        "wheel_url": wheel_url,
        "core_wheel_sha256": wanted,
        "profile_url": profile_url,
        "profile_sha256": sha256(profile_bytes),
        "artifacts": fetch_artifacts(profile, app, accepted),
    }
    candidate = {"wheel": wheel, "dir": directory, "version": version, "sha256": wanted}
    return report, profile, candidate


def make_bootstrap(args, profile, accepted, parent, candidate):
    mode = args.bootstrap
    if args.candidate_wheel_url:
        pinned = ["uvx", "--from", str(candidate["wheel"]), "heinzel"]
        return {
            "profile": profile,
            "accepted": accepted,
            "version": args.expect_version,
            "mode": "release-candidate",
            "command": pinned,
            "env": {"HZ_APP_PROFILE": args.candidate_profile_url},
        }
    published = {"profile": profile, "accepted": accepted, "version": args.expect_version, "mode": mode}
    pinned = ["uvx", "--from", str(candidate["wheel"]), "heinzel"] if candidate else None
    if mode == "literal":
        return {**published, "command": LITERAL_UVX, "env": {}}
    if mode == "explicit":
        url = f"{COLLECTION}/releases/download/app-v{args.expect_app_version}/standard.json"
        return {**published, "command": LITERAL_UVX, "env": {"HZ_APP_PROFILE": url}}
    if not candidate:
        sys.exit(f"--bootstrap {mode} needs --candidate-wheel.")
    if mode == "candidate-launcher":
        return {**published, "command": pinned, "env": {}}
    local, directory, path = candidate_app(candidate, profile, accepted, parent)
    return {
        "profile": local,
        "accepted": directory,
        "version": candidate["version"],
        "mode": mode,
        "command": pinned,
        "env": {"HZ_APP_PROFILE": str(path)},
        "core_wheel": directory / candidate["wheel"].name,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expect-version",
        required=True,
        help="Core version that PyPI latest must be. `published` takes whatever is published now (a dry run: the "
        "receipt records that the expectation was not supplied).",
    )
    parser.add_argument(
        "--expect-app-version",
        required=True,
        help="Standard app the collection must designate; `published` as for --expect-version.",
    )
    parser.add_argument(
        "--case", action="append", choices=sorted(CASES, key=int), help="Repeatable; default all, serially"
    )
    parser.add_argument("--out", type=Path, help="Also copy the JSON result here (no overwrite)")
    parser.add_argument(
        "--root-parent",
        type=Path,
        default=None,
        help="Where the private proof root is made (default: the runner's or system temp)",
    )
    parser.add_argument("--accept-wheel-sha256", help="Independently accepted Core wheel hash")
    parser.add_argument("--accept-profile-sha256", help="Independently accepted standard.json hash")
    parser.add_argument("--accept-install-sh-sha256", help="Accepted live install.sh hash if main moved past the tag")
    parser.add_argument(
        "--candidate-wheel", type=Path, help="A locally built Core wheel under test (never the release)"
    )
    parser.add_argument(
        "--candidate-wheel-url", help="A held final release's Core wheel on this project's release page"
    )
    parser.add_argument("--candidate-wheel-sha256", help="The SHA-256 that wheel must have")
    parser.add_argument("--candidate-profile-url", help="The app release's standard.json that selects that wheel")
    parser.add_argument(
        "--bootstrap",
        choices=BOOTSTRAPS,
        default="literal",
        help="How cases 6-12 install their starting app: the public command, a candidate launcher over the governed "
        "app, the published app by explicit profile, or a candidate app (candidate Core, published plugins). Anything "
        "but literal is a diagnostic run.",
    )
    parser.add_argument("--from-app-version", default="0.9.8", help="Published app the update starts from")
    parser.add_argument("--chrome", default=None, help="Chrome binary for the browser cases")
    parser.add_argument(
        "--chrome-no-sandbox",
        type=lambda value: value == "true",
        default=False,
        metavar="{true,false}",
        help="Start Chrome without its sandbox (a disposable hosted Linux runner that forbids user namespaces)",
    )
    parser.add_argument(
        "--diagnostic-explicit-profile",
        action="store_true",
        help="NOT ACCEPTANCE: set HZ_APP_PROFILE to the published profile so a release whose discovery fails can "
        "still exercise later checks. Forces the verdict NOT_ACCEPTANCE_DIAGNOSTIC.",
    )
    args = parser.parse_args(argv)
    given = [bool(args.candidate_wheel_url), bool(args.candidate_wheel_sha256), bool(args.candidate_profile_url)]
    if any(given) and not all(given):
        parser.error("--candidate-wheel-url, --candidate-wheel-sha256 and --candidate-profile-url go together")
    if args.candidate_wheel_url and (
        args.candidate_wheel
        or args.bootstrap != "literal"
        or args.diagnostic_explicit_profile
        or "published" in (args.expect_version, args.expect_app_version)
    ):
        parser.error("a release-candidate run takes explicit versions and no other candidate or diagnostic option")
    return args


def resolve_published(args):
    """`published` expectations become what is published now; returns whether the caller supplied them."""
    supplied = "published" not in (args.expect_version, args.expect_app_version)
    if args.expect_version == "published":
        args.expect_version = json.loads(fetch("https://pypi.org/pypi/heinzel/json", 8 * 1024 * 1024))["info"][
            "version"
        ]
    if args.expect_app_version == "published":
        args.expect_app_version = latest_app_version()
    return supplied


def main():
    args = parse_args()
    if not sys.flags.isolated or sys.flags.optimize or sys.version_info < (3, 11):
        sys.exit("Run with python3 -I (3.11+, no -O).")
    if sys.platform not in ("darwin", "linux"):
        sys.exit("This harness targets macOS and Linux.")
    os.umask(0o077)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)

    guardian = load_vendor("guardian.py", GUARDIAN_SHA256)
    provenance = load_vendor("artifact_provenance.py", PROVENANCE_SHA256)
    release_run = bool(args.candidate_wheel_url)
    root_parent = args.root_parent or default_root_parent()
    root_parent.mkdir(parents=True, exist_ok=True)
    parent = Path(tempfile.mkdtemp(prefix="hz-acceptance-", dir=root_parent)).resolve(strict=True)
    info = parent.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        sys.exit("The proof root is not private.")

    if release_run:
        mode = "candidate_release"
    elif args.diagnostic_explicit_profile:
        mode = "diagnostic_explicit_profile"
    else:
        mode = "acceptance_literal"
    host = (
        {"macos": platform.mac_ver()[0], "machine": platform.machine()}
        if sys.platform == "darwin"
        else {"os": platform.platform(), "machine": platform.machine()}
    )
    result = {
        "schema": 2,
        "harness_sha256": file_sha256(Path(__file__).resolve()),
        "guardian_sha256": GUARDIAN_SHA256,
        "provenance_sha256": PROVENANCE_SHA256,
        "mode": mode,
        "public_path_proof": mode == "acceptance_literal",
        "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "host": host,
        "ci": ci_record(),
        "proof_root": str(parent),
        "limits": LIMITS,
        "verdict": "INCOMPLETE",
        "cases": {},
    }
    if args.diagnostic_explicit_profile:
        result["limits"] = [
            "DIAGNOSTIC: HZ_APP_PROFILE selected the app, bypassing discovery. Nothing here is acceptance evidence "
            "for the public path.",
            *LIMITS,
        ]
    if release_run:
        result["limits"] = [
            "CANDIDATE: the bytes under test are a held release's, launched from the release wheel with the release "
            "profile as HZ_APP_PROFILE. This is not the public path: cases that run the literal public commands "
            "(1, 5, 13) are SKIPPED, and PyPI was not consulted.",
            *LIMITS,
        ]
    path = parent / "result.json"
    secrets = set()
    write_result(path, result, secrets)
    live_before = live_snapshot()
    result["isolation"] = {"live_before": live_before}
    try:
        result["host"]["service_manager"] = manager_gate()
    except Refused as error:
        result["host"]["service_manager"] = {"refused": str(error)}
    candidate = None
    try:
        if release_run:
            result["expectation_supplied"] = True
            report, profile, candidate = bind_release_candidate(args, parent)
        else:
            result["expectation_supplied"] = resolve_published(args)
            report, profile = preflight(args, parent / "accepted")
    except (StepFailed, OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        result["preflight_failure"] = redact(f"{type(error).__name__}: {error}")[:500]
        result["verdict"] = "PREFLIGHT_FAILED"
        write_result(path, result, secrets)
        copy_result(args, path)
        print(json.dumps({"verdict": result["verdict"], "why": result["preflight_failure"], "result": str(path)}))
        return exit_code(result["verdict"])
    result["published" if not release_run else "candidate_release"] = report
    if args.candidate_wheel:
        candidate = bind_candidate(args.candidate_wheel, parent)
    selected = sorted(args.case or CASES, key=int)
    from_app = None
    if "6" in selected:
        try:
            from_app = bind_old_app(args.from_app_version, parent / "accepted-from")
        except (StepFailed, OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
            result["from_app_failure"] = redact(f"{type(error).__name__}: {error}")[:300]
    target = None
    if candidate:
        target = {"mode": "candidate", "version": candidate["version"], "wheel": candidate["wheel"]}
    elif release_key(args.expect_version) > release_key(args.from_app_version):
        core = next(a for a in profile["artifacts"] if a["name"] == "heinzel")
        target = {"mode": "published", "version": args.expect_version, "wheel": parent / "accepted" / core["filename"]}
    result["lifecycle"] = {
        "bootstrap": "release-candidate" if release_run else args.bootstrap,
        "candidate": {"version": candidate["version"], "sha256": candidate["sha256"]} if candidate else None,
        "from_app": from_app and {"version": from_app["version"], "profile_sha256": from_app["profile_sha256"]},
        "update_target": target and {"mode": target["mode"], "version": target["version"]},
    }
    bootstrap_spec = make_bootstrap(args, profile, parent / "accepted", parent, candidate)
    public = bootstrap_spec if release_run else {"command": LITERAL_UVX, "env": {}, "mode": "literal"}
    ctx = {
        "args": args,
        "chrome": args.chrome or default_chrome(),
        "chrome_no_sandbox": args.chrome_no_sandbox,
        "candidate": candidate["wheel"] if candidate else None,
        "candidate_dir": candidate["dir"] if candidate else None,
        "from_app": from_app,
        "update_target": target,
        "bootstrap": bootstrap_spec,
        "public": public,
        "Guarded": guardian["Guarded"],
        "check_runtime": provenance["check_runtime"],
        "profile": profile,
        "accepted": parent / "accepted",
        "explicit_profile": (
            f"{COLLECTION}/releases/download/app-v{args.expect_app_version}/standard.json"
            if args.diagnostic_explicit_profile
            else None
        ),
    }
    logs_with_secrets = 0
    for ident in selected:
        print(f"case {ident} ({CASES[ident]}) ...", flush=True)
        record, found, root = run_case(ident, parent, ctx)
        secrets |= found
        logs_with_secrets += secrets_in_logs(root, found)
        result["cases"][ident] = record
        write_result(path, result, secrets)
        print(f"case {ident}: {record['status']} in {record['seconds']}s", flush=True)
    live_after = live_snapshot()
    result["isolation"].update(
        live_after=live_after,
        live_untouched=live_before == live_after,
        logs_containing_session_tokens=logs_with_secrets,
        environment="Built from nothing: only LANG, LC_ALL, TERM inherited; no credentials, no PYTHON*/UV_* overrides.",
    )
    result["verdict"] = verdict(args, result["cases"])
    if not result["isolation"]["live_untouched"] or logs_with_secrets:
        result["verdict"] = "REJECTED_ISOLATION" if logs_with_secrets else result["verdict"]
        result.setdefault("harness_warnings", []).append(
            "Live identity changed during this run; the harness does not touch it, but check for another actor."
            if not result["isolation"]["live_untouched"]
            else "A session token reached a log."
        )
    result["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    write_result(path, result, secrets)
    copy_result(args, path)
    print(json.dumps({"verdict": result["verdict"], "result": str(path)}))
    return exit_code(result["verdict"])


if __name__ == "__main__":
    sys.exit(main())
