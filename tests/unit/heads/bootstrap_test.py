"""One bare command installs a complete app once and opens its managed foreground workspace."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from heinzel import HeinzelError
from heinzel.heads import bootstrap, install
from heinzel.heads._tool_install import ToolTarget

pytestmark = pytest.mark.unit
WORKSPACE_COMMAND = bootstrap._workspace_command
COMPLETE_PROFILE = bootstrap._complete_profile
VERIFY_SETUP = bootstrap._verify_setup


@pytest.fixture(autouse=True)
def managed_workspace(monkeypatch):
    monkeypatch.setattr(bootstrap, "_workspace_command", lambda *_args: ["ui", "open"])
    monkeypatch.setattr(bootstrap, "_complete_profile", lambda *_args: None)
    monkeypatch.setattr(bootstrap, "_verify_setup", lambda *_args: None)


@pytest.mark.parametrize("already_installed", [False, True])
def test_first_use_installs_defaults_and_later_use_opens_without_installing(monkeypatch, tmp_path, already_installed):
    executable = tmp_path / "tools/heinzel/bin/hz"
    sequence = [] if already_installed else [None]
    installed, launched = [], []
    monkeypatch.setattr(bootstrap.sys, "argv", ["heinzel"])
    monkeypatch.setattr(bootstrap, "installed_app", lambda: sequence.pop(0) if sequence else executable)
    monkeypatch.setattr(install, "main", lambda args: installed.append(args) or 0)
    monkeypatch.setattr(bootstrap.os, "execve", lambda *args: launched.append(args))
    bootstrap.main()
    assert installed == ([] if already_installed else [[]])
    [program, args, environment] = launched[0]
    interpreter = str(executable.parent / "python")
    assert program == interpreter
    assert args == [interpreter, "-I", "-m", "heinzel", "ui", "open"]
    assert environment["PATH"].split(bootstrap.os.pathsep)[0] == str(executable.parent)


def test_explicit_arguments_keep_existing_cli_behavior(monkeypatch):
    from heinzel.heads import cli

    called = []
    monkeypatch.setattr(bootstrap.sys, "argv", ["heinzel", "--help"])
    monkeypatch.setattr(cli, "main", lambda: called.append(True))
    monkeypatch.setattr(bootstrap, "installed_app", lambda: pytest.fail("Explicit CLI arguments must not install"))
    bootstrap.main()
    assert called == [True]


@pytest.mark.parametrize("arguments", [["ui", "serve", "--no-browser"], ["join", "https://github.com/example/team"]])
def test_runtime_arguments_never_open_the_temporary_plugin_free_sdk(monkeypatch, tmp_path, arguments):
    executable = tmp_path / "managed/bin/hz"
    launched = []
    monkeypatch.setattr(bootstrap.sys, "argv", ["heinzel", *arguments])
    monkeypatch.setattr(bootstrap, "installed_app", lambda: executable)
    monkeypatch.setattr(bootstrap.os, "execve", lambda *args: launched.append(args))
    bootstrap.main()
    assert launched[0][1] == [str(executable.parent / "python"), "-I", "-m", "heinzel", *arguments]


def test_a_failed_install_never_launches_a_partial_app(monkeypatch, capsys):
    monkeypatch.setattr(bootstrap.sys, "argv", ["heinzel"])
    monkeypatch.setattr(bootstrap, "installed_app", lambda: None)
    monkeypatch.setattr(install, "main", lambda _args: 1)
    monkeypatch.setattr(bootstrap.os, "execve", lambda *_args: pytest.fail("Must not open a failed install"))
    with pytest.raises(SystemExit) as result:
        bootstrap.main()
    assert result.value.code == 1
    assert "Installing Heinzel and its default plugins" in capsys.readouterr().err


@pytest.mark.parametrize("matching", [False, True])
def test_current_continuity_governs_updated_versions_and_removed_membership_without_target_execution(
    tmp_path, monkeypatch, matching
):
    directory = tmp_path / "tools/heinzel/bin"
    directory.mkdir(parents=True)
    for name in ("hz", "python"):
        (directory / name).touch()
    data = tmp_path / "data"
    data.mkdir()
    names = ["heinzel", "heinzel-harbor"]
    profile = {
        "format": 2,
        "sdk": 1,
        "core_version": "1.0.0",
        "artifacts": [
            {
                "name": name,
                "version": "1.0.0",
                "filename": f"{name.replace('-', '_')}-1.0.0-py3-none-any.whl",
                "sha256": "a" * 64,
                "url": f"https://example.com/{name}.whl",
            }
            for name in names
        ],
    }
    (data / "standard-app.json").write_text(json.dumps(profile))
    packages = directory.parent / "lib/python3.12/site-packages"
    packages.mkdir(parents=True)
    # A successful independent update changed Core and intentionally dropped a formerly default provider.
    metadata = packages / "heinzel-2.0.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: heinzel\nVersion: 2.0.0\n")
    monkeypatch.setattr(
        bootstrap.Config, "load", lambda: SimpleNamespace(settings=lambda *_: SimpleNamespace(data_dir=data))
    )
    monkeypatch.setattr(install, "uv_executable", lambda: "/trusted/uv")
    (tmp_path / "bin").mkdir()
    monkeypatch.setattr(bootstrap.Installation, "verify_tool_destination", lambda *_args: None, raising=False)
    monkeypatch.setattr(bootstrap.Installation, "startup_state_matches", lambda _self: matching, raising=False)
    monkeypatch.setattr(
        bootstrap.Installation,
        "record_startup_state",
        lambda *_args, **_kwargs: pytest.fail("Launch inspection must never seed or refresh a checkpoint"),
        raising=False,
    )
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        assert command in (["/trusted/uv", "tool", "dir"], ["/trusted/uv", "tool", "dir", "--bin"])
        selected = tmp_path / ("bin" if command[-1] == "--bin" else "tools")
        return subprocess.CompletedProcess(command, 0, str(selected), "")

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    if matching:
        assert bootstrap.installed_app() == directory / "hz"
    else:
        with pytest.raises(HeinzelError, match="older composition will not be reapplied"):
            bootstrap.installed_app()
    assert len(commands) == 2


def test_missing_tool_directory_is_first_use(monkeypatch, tmp_path):
    monkeypatch.setattr(install, "uv_executable", lambda: "/trusted/uv")
    monkeypatch.setattr(bootstrap, "resolve_target", lambda _uv: ToolTarget(tmp_path / "tools", tmp_path / "bin"))
    assert bootstrap.installed_app() is None


def test_a_plain_sdk_without_an_app_receipt_can_be_converted(tmp_path, monkeypatch):
    target = ToolTarget(tmp_path / "tools", tmp_path / "bin")
    target.prefix.mkdir(parents=True)
    monkeypatch.setattr(install, "uv_executable", lambda: "/trusted/uv")
    monkeypatch.setattr(bootstrap, "resolve_target", lambda _uv: target)
    monkeypatch.setattr(
        bootstrap.Config,
        "load",
        lambda: SimpleNamespace(settings=lambda *_: SimpleNamespace(data_dir=tmp_path / "data")),
    )
    assert bootstrap.installed_app() is None


@pytest.mark.parametrize("store_name", ["original-tools", "other-tools"])
def test_a_stale_global_app_receipt_cannot_promote_an_unmarked_sdk(tmp_path, monkeypatch, store_name):
    target = ToolTarget(tmp_path / store_name, tmp_path / "bin")
    binary = target.prefix / "bin"
    binary.mkdir(parents=True)
    target.binary.mkdir()
    for name in ("hz", "python"):
        (binary / name).write_text("This target must never execute")
    shim = target.binary / "hz"
    shim.symlink_to(binary / "hz")
    (target.prefix / "uv-receipt.toml").write_text(
        f"[tool]\nrequirements=[{{name=\"heinzel\"}}]\nentrypoints=[{{name='hz', install-path={str(shim)!r}}}]\n"
    )
    data = tmp_path / "shared-data"
    data.mkdir()
    receipt = install.AppProfile(
        format=2,
        sdk=1,
        core_version="1.0.0",
        artifacts=[
            install.Artifact(
                name="heinzel",
                version="1.0.0",
                filename="heinzel-1.0.0-py3-none-any.whl",
                sha256="a" * 64,
                url="https://example.com/core.whl",
            )
        ],
    )
    (data / "standard-app.json").write_text(receipt.model_dump_json())
    monkeypatch.setattr(install, "uv_executable", lambda: "/trusted/uv")
    monkeypatch.setattr(bootstrap, "resolve_target", lambda _uv: target)
    monkeypatch.setattr(
        bootstrap.Config, "load", lambda: SimpleNamespace(settings=lambda *_: SimpleNamespace(data_dir=data))
    )
    monkeypatch.setattr(
        bootstrap.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("SDK inspection must not start installed code"),
    )
    with pytest.raises(HeinzelError, match="older composition will not be reapplied"):
        bootstrap.installed_app()


def test_an_unverified_existing_app_never_triggers_an_older_profile_reinstallation(monkeypatch, capsys):
    def refuse():
        msg = "The recorded installation does not match; inspect it explicitly."
        raise HeinzelError(msg)

    monkeypatch.setattr(bootstrap.sys, "argv", ["heinzel"])
    monkeypatch.setattr(bootstrap, "installed_app", refuse)
    monkeypatch.setattr(install, "main", lambda *_args: pytest.fail("A mismatch must not downgrade or adopt the app"))
    monkeypatch.setattr(bootstrap.os, "execve", lambda *_args: pytest.fail("An unverified app must not run"))
    with pytest.raises(SystemExit) as result:
        bootstrap.main()
    assert result.value.code == 1
    assert "inspect it explicitly" in capsys.readouterr().err


def test_first_use_json_stdout_contains_only_the_managed_command_result(monkeypatch, tmp_path, capsys):
    executable = tmp_path / "tools/heinzel/bin/hz"
    sequence = [None]
    monkeypatch.setattr(bootstrap, "installed_app", lambda: sequence.pop(0) if sequence else executable)
    monkeypatch.setattr(bootstrap.sys, "argv", ["heinzel", "system", "info", "--json"])
    profile = install.AppProfile(
        format=2,
        sdk=1,
        core_version="1.0.0",
        artifacts=[
            install.Artifact(
                name="heinzel",
                version="1.0.0",
                filename="heinzel-1.0.0-py3-none-any.whl",
                sha256="a" * 64,
                url="https://example.com/heinzel.whl",
            )
        ],
    )
    monkeypatch.setattr(install, "latest_profile", lambda: profile)
    monkeypatch.setattr(install, "uv_executable", lambda: "/trusted/uv")

    def prepare(_profile, directory, _excluded):
        directory.mkdir(parents=True)
        wheel = directory / profile.artifacts[0].filename
        wheel.write_bytes(b"unit fixture")
        return [wheel]

    monkeypatch.setattr(install, "prepare", prepare)

    def managed_install(selected_profile, _selected, paths, _settings, _exclusions):
        assert selected_profile is profile
        assert [path.name for path in paths] == [profile.artifacts[0].filename]
        bootstrap.sys.stderr.write("installer progress\n")
        return 0

    monkeypatch.setattr(install, "_managed_install", managed_install)
    monkeypatch.setattr(bootstrap.os, "execve", lambda *_args: bootstrap.sys.stdout.write('{"ok":true}\n'))
    bootstrap.main()
    output = capsys.readouterr()
    assert json.loads(output.out) == {"ok": True}
    assert "Installing Heinzel" in output.err
    assert "heinzel 1.0.0" in output.err


@pytest.mark.parametrize("daemon", [False, True])
@pytest.mark.parametrize("web", [False, True])
@pytest.mark.parametrize("notifier", [False, True])
def test_interactive_choices_are_separate_and_run_only_in_verified_persistent_python(
    monkeypatch, tmp_path, daemon, web, notifier
):
    from heinzel.heads.cli._setup import ConsolePrompter

    executable = tmp_path / "persistent-tools/heinzel/bin/hz"
    executable.parent.mkdir(parents=True)
    executable.touch()
    events = []
    questions = []
    answers = iter([daemon, web, notifier])
    monkeypatch.setattr(bootstrap.sys, "argv", ["heinzel"])
    monkeypatch.setattr(bootstrap.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(bootstrap.sys.stdout, "isatty", lambda: True)

    def confirm(_self, question, *, default):
        assert default is True
        questions.append(question)
        return next(answers)

    def verified():
        events.append("verified")
        return executable

    def run(command, **kwargs):
        assert events[-1] in {"verified", "install"}
        assert command[:4] == [str(executable.parent / "python"), "-I", "-m", "heinzel"]
        assert kwargs["env"]["PATH"].split(bootstrap.os.pathsep)[0] == str(executable.parent)
        events.append("install" if "install" in command else "status")
        calls.append(command[4:])
        if command[4:6] == ["system", "notify"]:
            value = {"notifier": None, "shown": False, "problem": "No desktop", "next": []}
        else:
            value = {"installed": True, "loaded": True, "ready": True, "executable": str(executable)}
        return subprocess.CompletedProcess(command, 0, json.dumps(value), "")

    calls = []
    monkeypatch.setattr(ConsolePrompter, "confirm", confirm)
    monkeypatch.setattr(bootstrap, "installed_app", verified)
    monkeypatch.setattr(install, "main", lambda *_args: pytest.fail("Verified installed app must be reused"))
    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    monkeypatch.setattr(bootstrap.os, "execve", lambda *_args: events.append("open"))
    bootstrap.main()
    assert len(questions) == 3
    expected = []
    if daemon:
        expected.extend(
            [
                ["system", "daemon", "install", "--no-notifier", "--json"],
                ["system", "daemon", "status", "--json"],
            ]
        )
    if web:
        expected.extend([["ui", "install", "--json"], ["ui", "status", "--json"]])
    if notifier:
        expected.append(["system", "notify", "install", "--json"])
    assert calls == expected
    assert events[-1] == "open"


@pytest.mark.parametrize("arguments", [["ui"], ["ui", "serve"], ["system", "info", "--json"]])
def test_explicit_runtime_and_json_commands_never_offer_service_installation(monkeypatch, arguments):
    from heinzel.heads.cli._setup import ConsolePrompter

    monkeypatch.setattr(bootstrap.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(bootstrap.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(ConsolePrompter, "confirm", lambda *_args, **_kwargs: pytest.fail("Unexpected consent prompt"))
    assert bootstrap._service_choices(arguments) == []


@pytest.mark.parametrize(("stdin", "stdout"), [(False, False), (True, False), (False, True)])
def test_noninteractive_bare_launcher_never_selects_services(monkeypatch, stdin, stdout):
    from heinzel.heads.cli._setup import ConsolePrompter

    monkeypatch.setattr(bootstrap.sys.stdin, "isatty", lambda: stdin)
    monkeypatch.setattr(bootstrap.sys.stdout, "isatty", lambda: stdout)
    monkeypatch.setattr(ConsolePrompter, "confirm", lambda *_args, **_kwargs: pytest.fail("Unexpected consent prompt"))
    assert bootstrap._service_choices([]) == []


@pytest.mark.parametrize("result", [{}, {"installed": True, "loaded": False}, {"installed": 1, "loaded": True}])
def test_selected_service_requires_observed_installed_and_loaded_before_opening(monkeypatch, tmp_path, result):
    executable = tmp_path / "persistent/bin/hz"
    monkeypatch.setattr(bootstrap, "installed_app", lambda: executable)
    monkeypatch.setattr(
        bootstrap.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, json.dumps(result), ""),
    )
    with pytest.raises(HeinzelError, match="could not be verified"):
        bootstrap._install_services(executable, [("ui",)], {})


@pytest.mark.parametrize(
    "result",
    [
        {"notifier": "heinzel-notifier", "state": "missing", "shown": False},
        {"notifier": "heinzel-notifier", "state": "outdated", "shown": False},
        {"notifier": "foreign", "shown": True},
        [],
    ],
)
def test_notifier_never_claims_an_unverified_companion_is_available(result):
    with pytest.raises(HeinzelError):
        bootstrap._notifier_result(result)


def test_notifier_permission_and_delivery_are_not_inferred_from_installation(capsys):
    bootstrap._notifier_result({"notifier": "heinzel-notifier", "state": "installed", "shown": False})
    assert "needs desktop or notification permission" in capsys.readouterr().err
    bootstrap._notifier_result({"notifier": "heinzel-notifier", "state": "current", "shown": True})
    assert "Check the actual notification" in capsys.readouterr().err


@pytest.mark.parametrize("modern", [False, True])
def test_offline_command_discovery_preserves_published_older_app_launches(monkeypatch, tmp_path, modern):
    discover = WORKSPACE_COMMAND
    commands = [{"command": "hz ui open"}, {"command": "hz ui serve"}] if modern else []
    document = {"schema_version": 1, "root": {"commands": [{"command": "hz ui", "commands": commands}]}}
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(document), "")

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    executable = tmp_path / "persistent/bin/hz"
    assert discover(executable, {}, []) == (["ui", "open"] if modern else ["ui"])
    if not modern:
        with pytest.raises(HeinzelError, match="predates"):
            discover(executable, {}, [("system", "daemon")])
    assert all(command[-1] == "--command-tree" for command in calls)


def test_selected_web_setup_observes_readiness_before_completion(monkeypatch, tmp_path):
    responses = iter(
        [{"installed": True, "loaded": True, "ready": False}, {"installed": True, "loaded": True, "ready": True}]
    )
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(next(responses)), "")

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _: None)
    status = bootstrap._service_status(tmp_path / "persistent/bin/hz", ("ui",), {})
    assert status["ready"] is True
    assert len(calls) == 2
    assert all(call[-2:] == ["status", "--json"] for call in calls)


def test_selected_web_setup_cannot_complete_after_readiness_deadline(monkeypatch, tmp_path):
    clock = iter([0, 0, 13])
    monkeypatch.setattr(bootstrap.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(
        bootstrap.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, json.dumps({"installed": True, "loaded": True, "ready": False}), ""
        ),
    )
    with pytest.raises(HeinzelError, match="not ready"):
        bootstrap._service_status(tmp_path / "persistent/bin/hz", ("ui",), {})


def test_managed_profile_commands_refuse_changed_installation_before_running(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "installed_app", lambda: None)
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("Unverified target ran"))
    with pytest.raises(HeinzelError, match="changed before setup"):
        bootstrap._managed_json(tmp_path / "ephemeral/bin/hz", {}, ["org", "show"])
