"""Golden transcripts of `uvx heinzel@latest`: the questions, the services and the last line, byte for byte.

`UPDATE_GOLDEN=1` rewrites `start_golden/*.txt`. A FakeLaunchd stands in for the managed interpreter: it records the
service plists it would write and answers `status --json` from them; the desktop installer is faked the same way.
"""

import json
import os
import re
from pathlib import Path

import pytest

from heinzel.heads import bootstrap

pytestmark = pytest.mark.unit
GOLDEN = Path(__file__).parent / "start_golden"
TREE = {
    "schema_version": 1,
    "root": {
        "commands": [
            {"command": "hz ui", "commands": [{"command": "hz ui open"}, {"command": "hz ui serve"}]},
            {"command": "hz app", "commands": [{"command": "hz app open"}, {"command": "hz app install"}]},
        ]
    },
}


class FakeLaunchd:
    """Records plists per service and answers the managed interpreter's JSON commands."""

    def __init__(self, executable: Path, *, app_ok: bool = True) -> None:
        self.executable, self.app_ok, self.loaded = executable, app_ok, []
        self.transcript: list[str] = []

    def run(self, command, **_kwargs):
        arguments = command[4:]
        out = ""
        code = 0
        if arguments == ["--command-tree"]:
            out = json.dumps(TREE)
        elif arguments[:2] == ["app", "install"]:
            self.transcript.append("[hz app install --yes]")
            code = 0 if self.app_ok else 1
        elif arguments[:2] == ["app", "doctor"]:
            out = json.dumps({"opens": self.app_ok})
        elif arguments[-1] == "--json" and "install" in arguments:
            service = " ".join(arguments[: arguments.index("install")])
            self.loaded.append(service)
            self.transcript.append(f"[launchd loaded: {service}]")
            out = json.dumps({"notifier": "heinzel-notifier", "state": "installed", "shown": True})
        elif arguments[-2:] == ["status", "--json"]:
            out = json.dumps(
                {"installed": True, "loaded": True, "ready": True, "owned": True, "executable": str(self.executable)}
            )
        return type("Result", (), {"returncode": code, "stdout": out, "stderr": ""})()


class Script:
    def __init__(self, answers: list[bool]) -> None:
        self.answers, self.asked = answers, []

    def confirm(self, question: str, *, default: bool) -> bool:
        self.asked.append(f"? {question} [{'Y/n' if default else 'y/N'}]")
        return self.answers.pop(0)


def _play(monkeypatch, tmp_path, capsys, options):
    argv, tty, answers = options["argv"], options["tty"], options.get("answers", ())
    installed_app, app_ok, silicon = options.get("installed_app", False), options.get("app_ok", True), True
    state = options.get("state")
    executable = tmp_path / "tool/bin/hz"
    executable.parent.mkdir(parents=True)
    executable.write_text("")
    (executable.parent / "python").write_text("")
    fake = FakeLaunchd(executable, app_ok=app_ok)
    script = Script(list(answers))
    launched: list[str] = []
    from heinzel.heads import _desktop_installation
    from heinzel.heads.cli import _setup

    monkeypatch.setattr(bootstrap.sys, "argv", ["heinzel", *argv])
    monkeypatch.setattr(bootstrap.sys.stdout, "isatty", lambda: tty)
    monkeypatch.setattr(bootstrap.sys.stdin, "isatty", lambda: tty)
    monkeypatch.setattr(bootstrap, "_interactive_launch", lambda args: tty and not args)
    monkeypatch.setattr(bootstrap, "_supports_app", lambda: silicon)
    monkeypatch.setattr(bootstrap, "_start_state", lambda: (state or tmp_path) / "state/start.json")
    monkeypatch.setattr(bootstrap, "_welcome", lambda: None)
    monkeypatch.setattr(bootstrap, "_ensure_app", lambda **_k: executable)
    monkeypatch.setattr(bootstrap, "installed_app", lambda: executable)
    monkeypatch.setattr(bootstrap, "_prepare_profile", lambda *_a, **_k: None)
    monkeypatch.setattr(bootstrap, "_verify_setup", lambda *_a: None)
    monkeypatch.setattr(bootstrap, "__version__", "0.0.0")
    monkeypatch.setattr(bootstrap.subprocess, "run", fake.run)
    monkeypatch.setattr(
        bootstrap.os, "execve", lambda _p, args, _e: launched.append("[exec hz " + " ".join(args[4:]) + "]")
    )
    monkeypatch.setattr(_setup, "ConsolePrompter", lambda _console: script)
    monkeypatch.setattr(
        _desktop_installation, "desktop_location", lambda: Path("/Applications/App.app") if installed_app else None
    )
    bootstrap.main()
    captured = capsys.readouterr()
    text = "\n".join(
        [*script.asked, *fake.transcript, *captured.err.splitlines(), *captured.out.splitlines(), *launched]
    )
    return re.sub(re.escape(str(tmp_path)), "<tmp>", text) + "\n"


def _check(name: str, text: str) -> None:
    path = GOLDEN / f"{name}.txt"
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert text == path.read_text(encoding="utf-8")


CASES = {
    "happy": {"argv": [], "tty": True, "answers": [True]},
    "decline": {"argv": [], "tty": True, "answers": [False]},
    "yes_tty": {"argv": ["--yes"], "tty": True},
    "yes_no_tty": {"argv": ["--yes"], "tty": False},
    "no_app": {"argv": ["--no-app"], "tty": True},
    "non_tty": {"argv": [], "tty": False},
    "rerun": {"argv": [], "tty": True, "installed_app": True},
    "app_fails": {"argv": ["--yes"], "tty": True, "app_ok": False},
}


@pytest.mark.parametrize("name", CASES)
def test_transcript(monkeypatch, tmp_path, capsys, name):
    options = dict(CASES[name])
    if name in {"happy", "decline", "rerun", "no_app"}:
        options["answers"] = [*options.get("answers", []), True, True, True]  # daemon, web, notifier follow the app
    _check(name, _play(monkeypatch, tmp_path, capsys, options))


def test_a_declined_app_is_not_asked_again_on_the_next_run(monkeypatch, tmp_path, capsys):
    _play(monkeypatch, tmp_path, capsys, {"argv": [], "tty": True, "answers": [False, True, True, True]})
    (tmp_path / "again").mkdir()
    options = {"argv": [], "tty": True, "answers": [True, True, True], "state": tmp_path}
    again = _play(monkeypatch, tmp_path / "again", capsys, options)
    assert "desktop app too" not in again
    assert (tmp_path / "state/start.json").exists()
