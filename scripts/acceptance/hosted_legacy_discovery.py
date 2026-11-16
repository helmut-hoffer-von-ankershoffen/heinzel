#!/usr/bin/env python3
"""One hosted diagnostic: literal case 6 first, then a separate probe only after its bridge-update failure."""

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SELECTOR = "tests/diagnostics/legacy_discovery_test.py"
FAILURE_STEP = "the published bridge updates to the target release"
PRIVATE_PATHS = {
    "HOME": "home",
    "TMPDIR": "tmp",
    "UV_TOOL_DIR": "tools",
    "UV_TOOL_BIN_DIR": "bin",
    "UV_PYTHON_INSTALL_DIR": "uv-python",
    "UV_CACHE_DIR": "uv-cache",
    "HZ_CONFIG_HOME": "config_home",
    "HZ_DATA_DIR": "data_dir",
    "HZ_CACHE_DIR": "cache_dir",
    "HZ_STATE_DIR": "state_dir",
    "HZ_LOG_DIR": "log_dir",
    "HZ_SYSTEM_DAEMON__UNIT_DIR": "units",
    "XDG_CACHE_HOME": "xdg-cache",
    "XDG_CONFIG_HOME": "xdg-config",
    "XDG_DATA_HOME": "xdg-data",
    "XDG_STATE_HOME": "xdg-state",
}


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require_hosted(environment, platform):
    expected = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_WORKFLOW": "Focused job",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "macOS",
        "GITHUB_RUN_ATTEMPT": "1",
        "FOCUSED_KIND": "pytest",
        "FOCUSED_SELECTOR": SELECTOR,
        "FOCUSED_REPEAT": "1",
        "FOCUSED_STRESS": "false",
    }
    if platform != "darwin" or any(environment.get(key) != value for key, value in expected.items()):
        raise ValueError("requires one unstressed original hosted macOS diagnostic")
    if not re.fullmatch(r"[0-9a-f]{40}", environment.get("GITHUB_SHA", "")):
        raise ValueError("exact source SHA required")
    if environment.get("FOCUSED_REF") != environment.get("GITHUB_REF_NAME") or not environment.get("FOCUSED_REF"):
        raise ValueError("dispatch branch mismatch")


def verify_source(repo, sha):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL)

    if git("rev-parse", "HEAD").decode().strip() != sha:
        raise ValueError("checkout SHA mismatch")
    files = [
        "scripts/acceptance/hosted_legacy_discovery.py",
        "scripts/acceptance/legacy_discovery_probe.py",
        "scripts/acceptance/run_acceptance.py",
        SELECTOR,
        ".github/workflows/focused-job.yml",
        "scripts/focused_job.py",
    ]
    hashes = {}
    for name in files:
        path = repo / name
        data = path.read_bytes()
        if path.is_symlink() or data != git("show", f"{sha}:{name}"):
            raise ValueError("diagnostic source differs from commit")
        hashes[name] = hashlib.sha256(data).hexdigest()
    return hashes


def write_once(path, data):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")


def eligible(record, ctx):
    return (
        record.get("status") == "FAIL"
        and record.get("first_failure", {}).get("step") == FAILURE_STEP
        and ctx.get("update_target", {}).get("mode") == "published"
    )


def prepare_probe(root):
    """Change only disposable bytecode/tool plumbing after the original failure has been saved."""
    root = root.resolve(strict=True)
    site = root / "tools/heinzel/lib/python3.12/site-packages"
    if not site.resolve(strict=True).is_relative_to(root):
        raise ValueError("case escaped")
    for path in (site / "heinzel").rglob("*"):
        if path.is_symlink():
            raise ValueError("unexpected Core symlink")
        if path.suffix == ".pyc":
            path.unlink()
    for path in (site / "__pycache__").glob("_heinzel_launcher.*.pyc"):
        if path.is_symlink():
            raise ValueError("unexpected launcher symlink")
        path.unlink()
    # Keep the harness's service-manager refusal even though the probe requires a private-bin-only PATH.
    for name in ("uv", "uvx", "launchctl", "systemctl"):
        target = root / "bin" / name
        if target.exists() or target.is_symlink():
            raise ValueError("unexpected private executable")
        shutil.copyfile((root / "guard" / name).resolve(strict=True), target)
        target.chmod(0o700)
    environment = {key: str(root / relative) for key, relative in PRIVATE_PATHS.items()}
    environment.update(
        PATH=f"{root}/bin:/usr/bin:/bin",
        UV_NO_CONFIG="1",
        HZ_UPDATE_CHECK="false",
        HZ_SYSTEM_CREDENTIAL__BACKEND="environment",
    )
    return environment


def follow_up(root, out, probe):
    environment = prepare_probe(root)
    command = [
        str(root / "tools/heinzel/bin/python"),
        "-I",
        "-S",
        str(HERE / "legacy_discovery_probe.py"),
        "--case-root",
        str(root),
        "--wheel",
        str(root / "bridge-wheel/heinzel-0.9.43-py3-none-any.whl"),
        "--confirm-dedicated-vm",
    ]
    # The reviewed probe supervises children and emits only its bounded sanitized result.
    done = subprocess.run(
        command,
        cwd=root,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=930,
        check=False,
    )
    result = probe.sanitized_result(json.loads(done.stdout)) if len(done.stdout) <= probe.OUTPUT_LIMIT else None
    write_once(out / "discovery-probe.json", {"kind": "post-failure-non-acceptance", "probe": result})


def attach(harness, out, probe):
    original = harness.run_case

    def observed(ident, parent, ctx):
        if ident == "6" and (out / "original-case6.json").exists():
            raise FileExistsError("case 6 already recorded")
        record, secrets, root = original(ident, parent, ctx)
        if ident == "6":
            # Save the genuine result before the follow-up mutates its disposable installation.
            harness.write_result(out / "original-case6.json", record, secrets)
            if eligible(record, ctx):
                write_once(out / "probe-intent.json", {"kind": "post-failure-non-acceptance", "attempt": 1})
                try:
                    follow_up(root, out, probe)
                except Exception:
                    write_once(
                        out / "probe-error.json", {"kind": "post-failure-non-acceptance", "status": "unavailable"}
                    )
            else:
                write_once(
                    out / "probe-not-run.json", {"kind": "non-acceptance", "reason": "no matching bridge failure"}
                )
        return record, secrets, root

    harness.run_case = observed


def main():
    require_hosted(os.environ, sys.platform)
    repo = HERE.parents[1]
    hashes = verify_source(repo, os.environ["GITHUB_SHA"])
    out = repo / "focused-artifacts/legacy-discovery"
    out.mkdir(parents=True, exist_ok=True)
    write_once(
        out / "diagnostic-intent.json", {"kind": "non-acceptance", "source": os.environ["GITHUB_SHA"], "hashes": hashes}
    )
    harness, probe = load("run_acceptance"), load("legacy_discovery_probe")
    harness.RECEIPT = out / "original-result.json"
    attach(harness, out, probe)
    sys.argv = [
        str(HERE / "run_acceptance.py"),
        "--expect-version",
        "published",
        "--expect-app-version",
        "published",
        "--case",
        "6",
    ]
    return harness.main()  # Preserve the original nonzero failure; never turn the probe into acceptance.


if __name__ == "__main__":
    raise SystemExit(main())
