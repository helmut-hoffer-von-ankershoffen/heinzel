"""The versionless `uvx heinzel@latest` quick start runs application commands in the managed app."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from pydantic import ValidationError

from heinzel import Config, HeinzelError, Progress, Prompter, Settings
from heinzel.heads import install
from heinzel.heads._tool_install import inspection_guidance, resolve_target
from heinzel.kernel import Installation


def installed_app() -> Path | None:
    """Find a verified current app without starting its interpreter or reverting independent changes.

    Raises:
        HeinzelError: If a marked installation no longer matches its recorded state.
    """
    uv = install.uv_executable()
    target = resolve_target(uv)
    directory = target.prefix
    if not directory.exists():
        return None
    executable = directory / "bin/hz"
    data = Config.load().settings(Settings, "hz").data_dir
    receipt = data / "standard-app.json"
    if not receipt.exists():
        return None  # An independently installed SDK can be converted without losing its extra requirements.
    reason = "The installed app could not be verified; an older composition will not be reapplied automatically"
    try:
        profile = install.read_profile(str(receipt))
        installation = Installation(Progress(), prefix=directory, uv=uv)
        installation.verify_tool_destination(target.store, target.binary)
        matching = (
            profile.format == install.PROFILE_FORMAT
            and executable.is_file()
            and (directory / "bin/python").is_file()
            and installation.startup_state_matches()
        )
    except (OSError, ValueError, HeinzelError) as exc:
        raise HeinzelError(inspection_guidance(target, uv, reason)) from exc
    if not matching:
        raise HeinzelError(inspection_guidance(target, uv, reason))
    return executable


def _service_choices(arguments: list[str]) -> list[tuple[str, ...]]:
    if arguments or not sys.stdin.isatty() or not sys.stdout.isatty():
        return []
    from rich.console import Console  # noqa: PLC0415 - interactive consent only

    from heinzel.heads.cli._setup import ConsolePrompter  # noqa: PLC0415 - reuse the CLI's explicit consent boundary

    prompter = ConsolePrompter(Console(file=sys.stderr))
    choices: list[tuple[str, ...]] = []
    if prompter.confirm("Install the daemon to run scheduled Heinzels after login?", default=True):
        choices.append(("system", "daemon"))
    if prompter.confirm("Install the web server to keep Heinzel available after login?", default=True):
        choices.append(("ui",))
    if prompter.confirm("Install native notifications for Heinzels on this computer?", default=True):
        choices.append(("system", "notify"))
    return choices


def _install_services(executable: Path, choices: list[tuple[str, ...]], environment: dict[str, str]) -> None:
    interpreter = str(executable.parent / "python")
    command = [interpreter, "-I", "-m", "heinzel"]
    for service in choices:
        if installed_app() != executable:
            msg = "The managed installation changed before service setup; inspect it before retrying."
            raise HeinzelError(msg)
        installed = subprocess.run(  # noqa: S603 - verified persistent interpreter and fixed service operation
            [*command, *service, "install", *(["--no-notifier"] if service == ("system", "daemon") else []), "--json"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        if installed.returncode:
            msg = f"Service installation did not complete. Inspect `hz {' '.join(service)} status` before retrying."
            raise HeinzelError(msg)
        if service == ("system", "notify"):
            result = json.loads(installed.stdout)
            _notifier_result(result)
            if installed_app() != executable:
                msg = "The managed installation changed during notifier setup; inspect it before launching."
                raise HeinzelError(msg)
            continue
        status = _service_status(executable, service, environment)
        if service == ("system", "daemon"):
            target = status.get("executable")
            if not isinstance(target, str) or not Path(target).samefile(executable):
                msg = (
                    "The daemon target does not match the verified persistent app; inspect its status before launching."
                )
                raise HeinzelError(msg)
        if installed_app() != executable:
            msg = "The managed installation changed during service setup; inspect it before launching."
            raise HeinzelError(msg)
        sys.stderr.write(f"{'Daemon' if service == ('system', 'daemon') else 'Web server'} installed and loaded.\n")


def _service_status(executable: Path, service: tuple[str, ...], environment: dict[str, str]) -> dict[str, object]:
    command = [str(executable.parent / "python"), "-I", "-m", "heinzel"]
    deadline = time.monotonic() + 12
    while True:
        observed = subprocess.run(  # noqa: S603 - same verified interpreter and read-only service inspection
            [*command, *service, "status", "--json"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=max(0.1, deadline - time.monotonic()),
        )
        status = json.loads(observed.stdout) if observed.returncode == 0 else {}
        if not isinstance(status, dict) or status.get("installed") is not True or status.get("loaded") is not True:
            msg = f"Service installation could not be verified. Inspect `hz {' '.join(service)} status`."
            raise HeinzelError(msg)
        if service != ("ui",) or status.get("ready") is True:
            break
        if time.monotonic() >= deadline:
            msg = "Setup is not complete: the owned web server is not ready. Inspect `hz ui status`."
            raise HeinzelError(msg)
        time.sleep(0.1)
    if status.get("version_mismatch") is True:
        msg = "Setup is not complete: the loaded service version does not match the installed app."
        raise HeinzelError(msg)
    return status


def _notifier_result(result: object) -> None:
    if not isinstance(result, dict) or "notifier" not in result or type(result.get("shown")) is not bool:
        msg = "Notifier setup returned no verifiable result. Inspect `hz system notify list --json`."
        raise HeinzelError(msg)
    if result.get("notifier") is None:
        sys.stderr.write(
            "Native notifications are unavailable on this platform or desktop. "
            "Run `hz system notify install` for guidance.\n"
        )
        return
    if result.get("notifier") == "heinzel-notifier" and result.get("state") not in {"current", "installed"}:
        msg = "The notifier companion could not be verified. Inspect `hz system notify install` before retrying."
        raise HeinzelError(msg)
    if result.get("notifier") not in {"heinzel-notifier", "notify-send", "osascript"}:
        msg = "The notifier identity could not be verified. Inspect `hz system notify list --json`."
        raise HeinzelError(msg)
    if result.get("shown") is True:
        sys.stderr.write(
            "Notifier available; its test was handed to the system. Check the actual notification on this computer.\n"
        )
    else:
        sys.stderr.write(
            "Notifier setup needs desktop or notification permission. Run `hz system notify install` for guidance.\n"
        )


def _workspace_command(executable: Path, environment: dict[str, str], choices: list[tuple[str, ...]]) -> list[str]:
    discovery = subprocess.run(  # noqa: S603 - read-only offline discovery in the verified managed interpreter
        [str(executable.parent / "python"), "-I", "-m", "heinzel", "--command-tree"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if discovery.returncode:
        msg = "The installed app's commands could not be verified; inspect `hz --command-tree` before launching."
        raise HeinzelError(msg)
    tree = json.loads(discovery.stdout)
    if not isinstance(tree, dict) or tree.get("schema_version") != 1 or not isinstance(tree.get("root"), dict):
        msg = "The installed app returned an unknown command schema; inspect it before launching."
        raise HeinzelError(msg)
    children = tree["root"].get("commands", [])
    if not isinstance(children, list):
        msg = "The installed app returned invalid command discovery; inspect it before launching."
        raise HeinzelError(msg)
    ui = next((item for item in children if isinstance(item, dict) and item.get("command") == "hz ui"), {})
    if not ui or not isinstance(ui.get("commands"), list):
        msg = "The installed app's web commands are unavailable; inspect `hz --command-tree`."
        raise HeinzelError(msg)
    commands = {
        item["command"] for item in ui["commands"] if isinstance(item, dict) and isinstance(item.get("command"), str)
    }
    if {"hz ui open", "hz ui serve"}.issubset(commands):
        return ["ui", "open"]
    if choices:
        msg = "The installed standard app predates these service choices. Update it before installing services."
        raise HeinzelError(msg)
    return ["ui"]  # A verified older standard app retains its published foreground entry point.


def _managed_json(executable: Path, environment: dict[str, str], arguments: list[str]) -> object:
    if installed_app() != executable:
        msg = "The managed installation changed before setup; inspect it before continuing."
        raise HeinzelError(msg)
    result = subprocess.run(  # noqa: S603 - verified persistent interpreter and fixed supported CLI operation
        [str(executable.parent / "python"), "-I", "-m", "heinzel", *arguments, "--json"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode:
        msg = "Setup could not verify the managed command's result; inspect `hz system doctor` before continuing."
        raise HeinzelError(msg)
    return json.loads(result.stdout)


def _complete_profile(executable: Path, environment: dict[str, str], prompter: Prompter | None = None) -> None:
    if prompter is None:
        from rich.console import Console  # noqa: PLC0415 - interactive setup only

        from heinzel.heads.cli._setup import ConsolePrompter  # noqa: PLC0415 - same terminal boundary as service consent

        prompter = ConsolePrompter(Console(file=sys.stderr))
    prompter.say("Your profile stays on this computer. Existing values will be kept.")
    profile = _managed_json(executable, environment, ["org", "show"])
    report = _managed_json(executable, environment, ["system", "config", "show"])
    if not isinstance(profile, dict) or not isinstance(profile.get("missing"), list) or not isinstance(report, dict):
        msg = "The profile requirements could not be verified; run `hz org show --json` before continuing."
        raise HeinzelError(msg)
    settings = report.get("settings")
    if not isinstance(settings, list):
        msg = "The profile settings could not be verified; inspect `hz system config show`."
        raise HeinzelError(msg)
    if not all(isinstance(key, str) for key in profile["missing"]):
        msg = "The profile declared invalid requirements; inspect `hz org show`."
        raise HeinzelError(msg)
    required = ["org.name", "team.name", *profile["missing"]]
    for key in dict.fromkeys(required):
        if not isinstance(key, str) or not key.startswith(("org.", "team.")):
            msg = "The profile declared unexpected requirements; inspect `hz org show`."
            raise HeinzelError(msg)
        field = next(
            (item for item in settings if isinstance(item, dict) and f"{item.get('section')}.{item.get('key')}" == key),
            None,
        )
        if field is None:
            msg = f"The required profile setting {key} is unavailable; inspect configuration before continuing."
            raise HeinzelError(msg)
        value = field.get("value")
        if value is not None and (not isinstance(value, str) or value.strip()):
            continue
        schema = field.get("json_schema") or {}
        variants = [schema, *schema.get("anyOf", [])] if isinstance(schema, dict) else []
        options: list[object] = next(
            (item["enum"] for item in variants if isinstance(item, dict) and isinstance(item.get("enum"), list)), []
        )
        choices = [item for item in options if isinstance(item, str)]
        title = {
            "org.name": "Your name",
            "team.name": "Team name",
            "org.handle": "Handle",
            "org.role": "Your role",
            "org.timezone": "Time zone (IANA, e.g. Europe/Berlin)",
        }.get(key, key)
        answer = (
            prompter.choice(title, choices)
            if choices
            else prompter.text(
                title, validate=lambda value: None if value.strip() else "Enter a real value to complete your profile."
            )
        )
        _managed_json(executable, environment, ["system", "config", "set", key, json.dumps(answer)])
    prompter.say("Profile saved through Heinzel configuration. Existing values were preserved.")


def _verify_setup(executable: Path, environment: dict[str, str]) -> None:
    health = _managed_json(executable, environment, ["system", "doctor"])
    if not isinstance(health, dict) or health.get("status") != "up":
        msg = "Setup is not complete: system health is not green. Run `hz system doctor` and resolve its real findings."
        raise HeinzelError(msg)
    sys.stderr.write("Setup complete; system health is up.\n")


def _launch_app(executable: Path, workspace: list[str], environment: dict[str, str]) -> None:
    if installed_app() != executable:
        msg = "The managed app changed before launch; inspect it before continuing."
        raise HeinzelError(msg)
    interpreter = str(executable.parent / "python")
    os.execve(  # noqa: S606 - selected managed interpreter and isolated module; no shell
        interpreter, [interpreter, "-I", "-m", "heinzel", *workspace], environment
    )


def main() -> None:
    """Install the verified standard app, offer separate interactive services, and open its workspace."""
    arguments = sys.argv[1:]
    if any(argument in {"--help", "-h", "--version", "-V", "--command-tree"} for argument in arguments):
        from heinzel.heads.cli import main as cli_main  # noqa: PLC0415 - argument-driven CLI remains independent

        cli_main()
        return
    try:
        interactive = not arguments and sys.stdin.isatty() and sys.stdout.isatty()
        choices = _service_choices(arguments)
        executable = installed_app()
        if executable is None:
            sys.stderr.write("Installing Heinzel and its default plugins…\n")
            status = install.main([])
            if status:
                sys.exit(status)
            executable = installed_app()
        if executable is None:
            sys.stderr.write("Installation did not leave a usable standard app. Retry `uvx heinzel@latest`.\n")
            sys.exit(1)
        environment = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
        environment["PATH"] = f"{executable.parent}{os.pathsep}{os.environ.get('PATH', '')}"
        workspace = arguments or _workspace_command(executable, environment, choices)
        if interactive and workspace == ["ui", "open"]:
            _complete_profile(executable, environment)
        _install_services(executable, choices, environment)
        if interactive and workspace == ["ui", "open"]:
            _verify_setup(executable, environment)
        _launch_app(executable, workspace, environment)
    except KeyboardInterrupt:
        sys.stderr.write(
            "Setup interrupted. Inspect service status and notifier setup before retrying; "
            "completed choices are preserved.\n"
        )
        sys.exit(130)
    except HeinzelError as error:
        sys.stderr.write(f"Heinzel could not start. {error}\n")
        sys.exit(1)
    except (OSError, ValueError, ValidationError, subprocess.SubprocessError):
        sys.stderr.write(
            "Heinzel could not start. Check your uv installation and connection, then retry `uvx heinzel@latest`.\n"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
