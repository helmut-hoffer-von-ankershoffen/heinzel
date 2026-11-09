import errno
import fcntl
import hashlib
import json
import os
import struct
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from heinzel.heads.mcp import _client_observer as protocol
from heinzel.heads.mcp import _client_observer_install as admission
from heinzel.heads.mcp._client_config import ClientBridgeBinding, ClientConfigContext, McpClient
from heinzel.kernel import HeinzelError

pytestmark = pytest.mark.unit


@pytest.fixture
def protected(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    header = b"\xcf\xfa\xed\xfe" if sys.platform == "darwin" else b"\x7fELF"

    def pinned(name, data, mode=0o644):
        path = root / name
        path.write_bytes(data)
        path.chmod(mode)
        return admission.ProtectedObserverFile(path, hashlib.sha256(data).hexdigest())

    lock = root / "update.lock"
    lock.touch()
    installation = admission.ProtectedObserverInstall(
        McpClient.CLAUDE,
        pinned("observer", header + b"observer", 0o755),
        pinned("vendor", header + b"vendor", 0o755),
        pinned("sandbox", b"fixture confinement"),
        (pinned("dependency", b"fixture dependency"),),
        lock,
        (lock.stat().st_dev, lock.stat().st_ino),
    )

    def open_fixture(path, stack):
        assert path.parent == root
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        stack.callback(os.close, descriptor)
        return descriptor

    # This fixture substitutes the OS ownership proof. The real descriptor policy is tested separately.
    monkeypatch.setattr(admission, "_open_protected", open_fixture)
    monkeypatch.setattr(admission.os, "getuid", lambda: 1001)
    monkeypatch.setattr(admission.os, "geteuid", lambda: 1001)
    return installation


def test_admission_holds_the_package_update_lock(protected):
    with protected.admitted(), protected.update_lock.open("rb") as other, pytest.raises(BlockingIOError):
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with protected.update_lock.open("rb") as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(HeinzelError, match="installation cannot be verified"), protected.admitted():
            pytest.fail("exclusive installer lock must prevent observation")


def test_admission_rejects_unpinned_dependency(protected):
    protected.dependencies[0].path.write_bytes(b"unreviewed dependency")
    with pytest.raises(HeinzelError, match="installation cannot be verified"), protected.admitted():
        pytest.fail("an unknown dependency must refuse before the helper starts")


def test_completed_update_requires_fresh_inventory(protected):
    policy_bytes = b"next reviewed policy"
    with protected.update_lock.open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        protected.policy.path.write_bytes(policy_bytes)
        current = replace(
            protected,
            policy=replace(protected.policy, sha256=hashlib.sha256(policy_bytes).hexdigest()),
        )
    assert current.digest != protected.digest
    with pytest.raises(HeinzelError, match="installation cannot be verified"), protected.admitted():
        pytest.fail("stale inventory must refuse a completed package update")
    with current.admitted():
        assert current.update_lock_identity == protected.update_lock_identity


@pytest.mark.parametrize("replace_lock", [True, False])
def test_admission_rejects_replaced_lock_and_image(protected, replace_lock):
    path = protected.update_lock if replace_lock else protected.vendor.path
    replacement = path.with_suffix(".replacement")
    replacement.write_bytes(path.read_bytes())
    replacement.chmod(path.stat().st_mode)
    with pytest.raises(HeinzelError, match="installation cannot be verified"), protected.admitted():
        replacement.replace(path)
    if replace_lock:
        with pytest.raises(HeinzelError, match="installation cannot be verified"), protected.admitted():
            pytest.fail("an existing package pin must refuse a replacement lock inode")


def test_admission_rejects_root_and_script_launcher(protected, monkeypatch):
    script = b"#!/bin/sh\nexit 0\n"
    protected.observer.path.write_bytes(script)
    script_install = replace(protected, observer=replace(protected.observer, sha256=hashlib.sha256(script).hexdigest()))
    with pytest.raises(HeinzelError, match="installation cannot be verified"), script_install.admitted():
        pytest.fail("a script delegates executable authority to an unpinned interpreter")
    monkeypatch.setattr(admission.os, "geteuid", lambda: 0)
    with pytest.raises(HeinzelError, match="installation cannot be verified"), protected.admitted():
        pytest.fail("the observer must never execute with elevated credentials")


def _acl(*flags, attrs=0x80400000):
    entries = b"".join(struct.pack("=16sII", bytes(16), flag, 0) for flag in flags)
    security = struct.pack("=I16s16sII", 0x012CC16D, bytes(16), bytes(16), len(flags), 0) + entries
    return struct.pack("=6IiI", 32 + len(security), attrs, 0, 0, 0, 0, 8, len(security)) + security


def test_darwin_requires_explicit_well_formed_non_granting_acl():
    assert admission._deny_only_acl(_acl())
    assert admission._deny_only_acl(_acl(2, 0x12))
    assert not admission._deny_only_acl(_acl(1))
    assert not admission._deny_only_acl(_acl(2, 1))
    assert not admission._deny_only_acl(_acl(attrs=0x80000000))
    assert not admission._deny_only_acl(_acl(2)[:-1])
    assert not admission._deny_only_acl(bytes(4096))


@pytest.mark.skipif(sys.platform != "darwin", reason="requires Darwin's descriptor ACL API")
def test_macos_reads_actual_acl_on_a_scratch_file(tmp_path):
    path = tmp_path / "acl"
    path.touch()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        assert not admission._darwin_acl(descriptor)
        subprocess.run(["/bin/chmod", "+a", "everyone deny delete", str(path)], check=True, timeout=5)
        assert admission._darwin_acl(descriptor)
        subprocess.run(["/bin/chmod", "+a", "everyone allow write", str(path)], check=True, timeout=5)
        assert not admission._darwin_acl(descriptor)
    finally:
        os.close(descriptor)


@pytest.mark.skipif(sys.platform != "darwin", reason="requires the macOS sealed system volume")
def test_protected_system_binary_and_owner_writable_ancestry(tmp_path):
    path = tmp_path.resolve() / "native"
    path.write_bytes(b"unprotected fixture")
    path.chmod(0o755)
    with ExitStack() as stack:
        image = admission._open_protected(Path("/usr/bin/true"), stack)
        admission._require_native_image(image)
        assert os.fstat(image).st_uid == 0
        with pytest.raises(HeinzelError, match="installation cannot be verified"):
            admission._open_protected(path, stack)


def test_linux_acl_and_capability_errors_refuse(monkeypatch):
    def unavailable(_descriptor, _name):
        raise OSError(errno.ENOTSUP, "unsupported")

    monkeypatch.setattr(admission.os, "getxattr", unavailable, raising=False)
    assert not admission._linux_attribute_absent(0, "security.capability")

    def absent(_descriptor, _name):
        raise OSError(errno.ENODATA, "absent")

    monkeypatch.setattr(admission.os, "getxattr", absent)
    assert admission._linux_attribute_absent(0, "system.posix_acl_access")


def _context(client=McpClient.CLAUDE):
    return ClientConfigContext(client, Path("/fixture/home"), Path("/fixture/workspace"))


def _request(context):
    return {
        "version": 1,
        "operation": "inspect",
        "challenge": "fixture nonce",
        "installation": "fixture install",
        "client": context.client.value,
        "home": str(context.home),
        "workspace": str(context.workspace),
        "server": "heinzel-fixture",
    }


def _response(request, context):
    return {
        "request": request,
        "confinement": "read-only-no-untrusted-exec",
        "managed_policy": protocol._managed_policy_evidence(),
        "inspection": {
            "returncode": 0,
            "stdout": "fixture output",
            "stderr": "",
            "source": str(context.path),
            "codex_config_read": None,
        },
    }


def _encode(payload):
    return json.dumps(payload).encode() + b"\n"


@pytest.mark.parametrize("failure", ["policy", "missing-policy", "scope", "nonce", "type", "source", "extra-frame"])
def test_observation_refuses_incomplete_or_mismatched_evidence(failure):
    context = _context()
    request = _request(context)
    response = _response(dict(request), context)
    if failure == "policy":
        response["managed_policy"]["status"] = "unavailable"
    elif failure == "missing-policy":
        del response["managed_policy"]
    elif failure == "scope":
        response["request"]["workspace"] = "/fixture/other"
    elif failure == "nonce":
        response["request"]["challenge"] = "prior nonce"
    elif failure == "type":
        response["request"]["version"] = True
    elif failure == "source":
        response["inspection"]["source"] = "/fixture/project-config"
    encoded = _encode(response)
    if failure == "extra-frame":
        encoded += _encode(response)
    with pytest.raises(HeinzelError, match="config observation is unavailable"):
        protocol._inspection(context, request, encoded)


def test_codex_requires_its_native_layer_evidence_in_the_protected_response():
    context = _context(McpClient.CODEX)
    request = _request(context)
    response = _response(request, context)
    with pytest.raises(HeinzelError, match="config observation is unavailable"):
        protocol._inspection(context, request, _encode(response))
    response["inspection"]["codex_config_read"] = "{}"
    result = protocol._inspection(context, request, _encode(response))
    assert result.context == context
    assert result.codex_config_read == "{}"  # the independent native parser checks actual layer content


def test_bounded_probe_passes_only_read_only_args_and_a_fresh_challenge(protected, monkeypatch):
    context = _context()
    binding = ClientBridgeBinding("fixture", Path("/fixture/python"), Path("/fixture/bridge.py"))
    callbacks = []

    def fixture_account(actual):
        assert actual == context

    @contextmanager
    def fixture_admission(_install):
        callbacks.append("admitted")
        yield
        callbacks.append("closed")

    def exchange(executable, encoded):
        assert executable == protected.observer.path
        request = json.loads(encoded)
        assert request.keys() == _request(context).keys()
        assert request["installation"] == protected.digest
        assert request["server"] == binding.name
        assert request["operation"] == "inspect"
        callbacks.append(request["challenge"])
        return _encode(_response(request, context))

    monkeypatch.setattr(protocol, "_require_account", fixture_account)
    monkeypatch.setattr(admission.ProtectedObserverInstall, "admitted", fixture_admission)
    monkeypatch.setattr(protocol, "_run", exchange)
    probe = protocol.ProtectedNativeObservation(protected)
    assert probe(context, binding).source == context.path
    assert probe(context, binding).source == context.path
    assert callbacks[::3] == ["admitted", "admitted"]
    assert callbacks[2::3] == ["closed", "closed"]
    assert callbacks[1] != callbacks[4]
    assert all(len(challenge) == 64 for challenge in callbacks[1::3])


@pytest.mark.parametrize(
    ("scenario", "program"),
    [
        ("ok", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"),
        ("limit", "import sys; sys.stdout.buffer.write(b'x' * 5000); sys.stdout.flush()"),
        ("timeout", "import time; time.sleep(60)"),
    ],
)
def test_native_helper_process_bounds_and_neutral_environment(monkeypatch, scenario, program):
    original_popen = subprocess.Popen
    processes = []

    def start(args, **kwargs):
        assert args == ["/fixture/observer", "observe-mcp-config"]
        assert kwargs["cwd"] == "/"
        assert kwargs["env"] == {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
        assert kwargs["close_fds"]
        assert kwargs["start_new_session"]
        assert kwargs["stderr"] == subprocess.DEVNULL
        child = original_popen([sys.executable, "-I", "-S", "-c", program], **kwargs)
        processes.append(child)
        return child

    monkeypatch.setattr(protocol.subprocess, "Popen", start)
    monkeypatch.setattr(protocol, "_MAX_RESPONSE", 1024)
    monkeypatch.setattr(protocol, "_TIMEOUT", 1.0)
    if scenario == "ok":
        assert protocol._run(Path("/fixture/observer"), b"request\n") == b"request\n"
    else:
        with pytest.raises(HeinzelError, match="config observation is unavailable"):
            protocol._run(Path("/fixture/observer"), b"request\n")
    assert len(processes) == 1
    assert processes[0].returncode is not None
    assert processes[0].stdin.closed
    assert processes[0].stdout.closed
