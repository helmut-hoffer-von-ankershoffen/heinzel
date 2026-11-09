"""The reviewed fixture bootstrap can only serve its retained private listener."""

import errno
import http.client
import os
import re
import select
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from heinzel.heads.web import capture
from heinzel.kernel import HeinzelError

pytestmark = [pytest.mark.unit, pytest.mark.skipif(sys.platform not in {"darwin", "linux"}, reason="POSIX capture")]


def profile_env(profile: Path) -> dict[str, str]:
    return {
        "HOME": str(profile),
        "HZ_CONFIG_HOME": str(profile / "config_home"),
        "HZ_DATA_DIR": str(profile / "data_dir"),
        "HZ_CACHE_DIR": str(profile / "cache_dir"),
        "HZ_STATE_DIR": str(profile / "state_dir"),
        "HZ_LOG_DIR": str(profile / "log_dir"),
        "HZ_SYSTEM_CREDENTIAL__BACKEND": "environment",
        "HZ_UPDATE_CHECK": "false",
        "HZ_PLUGINS_ENABLED": "false",
    }


def test_child_serves_asgi_on_the_retained_listener_and_does_not_retire_parent_socket(tmp_path):
    profile = tmp_path.resolve() / "fixture"
    profile.mkdir(mode=0o700)
    static = profile / "static"
    static.mkdir()
    (static / "index.html").write_text("<p>Isolated fixture</p>")
    with socket.socket() as parent:
        parent.bind(("127.0.0.1", 0))
        parent.listen()
        port = parent.getsockname()[1]
        code = (
            "import sys; from pathlib import Path; from heinzel.heads.web.capture import serve_capture_fixture; "
            "serve_capture_fixture(int(sys.argv[1]), port=int(sys.argv[2]), profile=Path.cwd(), "
            "static=Path('static'), announce=lambda url: print(url, flush=True))"
        )
        with subprocess.Popen(
            [sys.executable, "-I", "-c", code, str(parent.fileno()), str(port)],
            pass_fds=(parent.fileno(),),
            cwd=profile,
            env=profile_env(profile),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        ) as child:
            try:
                assert child.stdout is not None
                assert select.select([child.stdout], [], [], 3)[0]
                assert re.fullmatch(
                    rf"http://127\.0\.0\.1:{port}/#token=[A-Za-z0-9_-]{{43}}\n", child.stdout.readline()
                )
                client = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                try:
                    client.request("GET", "/")
                    response = client.getresponse()
                    assert response.status == 200
                    assert b"Isolated fixture" in response.read()
                finally:
                    client.close()
            finally:
                child.terminate()
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=2)
        with (
            socket.socket() as replacement,
            pytest.raises(OSError, check=lambda error: error.errno == errno.EADDRINUSE),
        ):
            replacement.bind(("127.0.0.1", port))
        parent.settimeout(1)
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            peer, _ = parent.accept()
            peer.close()


@pytest.mark.parametrize("kind", ["wrong-port", "wildcard", "udp", "bound-only", "reusable", "unix"])
def test_listener_refusal_preserves_the_untrusted_descriptor(kind):
    family = socket.AF_UNIX if kind == "unix" else socket.AF_INET
    socket_type = socket.SOCK_DGRAM if kind == "udp" else socket.SOCK_STREAM
    with socket.socket(family, socket_type) as listener:
        if family == socket.AF_INET:
            if kind == "reusable":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # A wildcard socket is never put in listen mode, even in this refusal test.
            listener.bind(("0.0.0.0" if kind == "wildcard" else "127.0.0.1", 0))  # noqa: S104
        if socket_type == socket.SOCK_STREAM and family == socket.AF_INET and kind not in {"bound-only", "wildcard"}:
            listener.listen()
        port = 0 if family == socket.AF_UNIX else listener.getsockname()[1]
        wrong = 1024 if port != 1024 else 1025
        with pytest.raises(HeinzelError, match="inherited exclusive"):
            capture._adopt_listener(listener.fileno(), wrong if kind == "wrong-port" or not port else port)
        os.fstat(listener.fileno())


def test_connected_tcp_descriptor_cannot_stand_in_for_the_listener():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with socket.create_connection(listener.getsockname(), timeout=1) as connection:
            port = connection.getsockname()[1]
            with pytest.raises(HeinzelError, match="inherited exclusive"):
                capture._adopt_listener(connection.fileno(), port)
            os.fstat(connection.fileno())


def test_non_socket_and_standard_descriptors_are_refused_and_retained(tmp_path):
    with (tmp_path / "regular-file").open("wb") as file:
        with pytest.raises(HeinzelError, match="inherited exclusive"):
            capture._adopt_listener(file.fileno(), 28005)
        os.fstat(file.fileno())
    with pytest.raises(HeinzelError, match="inherited exclusive"):
        capture._adopt_listener(2, 28005)


def test_successful_adoption_marks_child_copy_close_on_exec_and_only_closes_that_copy():
    with socket.socket() as parent:
        parent.bind(("127.0.0.1", 0))
        parent.listen()
        descriptor = os.dup(parent.fileno())
        os.set_inheritable(descriptor, True)  # noqa: FBT003 - simulate an inherited descriptor
        with capture._adopt_listener(descriptor, parent.getsockname()[1]) as adopted:
            assert adopted.fileno() == descriptor
            assert not adopted.get_inheritable()
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.fstat(descriptor)
        os.fstat(parent.fileno())


@pytest.mark.parametrize("kind", ["wrong-home", "symlink", "resume", "external-config", "wide-mode", "ancestor-config"])
def test_invalid_fixture_profile_is_refused_without_taking_a_listener(tmp_path, monkeypatch, kind):
    profile = tmp_path.resolve() / "fixture"
    profile.mkdir(mode=0o700)
    monkeypatch.chdir(profile)
    for key, value in profile_env(profile).items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("HZ_CONFIG", raising=False)
    monkeypatch.delenv(capture.RESUME, raising=False)
    if kind == "symlink":
        (profile / "state_dir").symlink_to(tmp_path, target_is_directory=True)
    elif kind == "wide-mode":
        profile.chmod(0o755)
    elif kind == "ancestor-config":
        (profile.parent / "hz.toml").write_text("[invalid")
    else:
        key = {"wrong-home": "HOME", "resume": capture.RESUME, "external-config": "HZ_CONFIG"}[kind]
        monkeypatch.setenv(key, str(tmp_path))
    error = "isolated configuration" if kind == "ancestor-config" else "private fixture profile"
    with pytest.raises(HeinzelError, match=error):
        capture.serve_capture_fixture(-1, port=28005, profile=profile)
