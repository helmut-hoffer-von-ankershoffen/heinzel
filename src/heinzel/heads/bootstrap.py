"""The versionless `uvx heinzel@latest` quick start runs application commands in the managed app."""

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from packaging.version import Version
from pydantic import JsonValue, TypeAdapter, ValidationError

from heinzel import Config, HeinzelError, Progress, Prompter, Settings, __version__
from heinzel.heads import _start_messages as words
from heinzel.heads import install
from heinzel.heads._tool_install import inspection_guidance, resolve_target
from heinzel.kernel import FILENAME, Installation
from heinzel.kernel._logging import get_logger, log_event

if TYPE_CHECKING:
    from rich.console import Console

_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
_JSON_OBJECTS = TypeAdapter(list[dict[str, JsonValue]])
_STRINGS = TypeAdapter(list[str])
_JSON_STRING = TypeAdapter(str)
log = get_logger("start")


def _event(name: str, **fields: object) -> None:
    """One structured start event: a stable dotted name and fields, never a formatted sentence or a secret."""
    log_event(log, name, **fields)  # type: ignore[arg-type]  # start fields never include level or exc_info


_START_FLAGS = ("--yes", "--no-app", "--ask")
_APP_WORKSPACE = ["app", "open"]


def _start_options(arguments: list[str]) -> tuple[list[str], set[str]]:
    """Split the start flags (`--yes`, `--no-app`, `--ask`) from the arguments delegated to the managed CLI."""
    return [item for item in arguments if item not in _START_FLAGS], {
        item for item in arguments if item in _START_FLAGS
    }


def _supports_app() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def _start_state() -> Path:
    return Config.load(Path.home()).settings(Settings, "hz").state_dir / "start.json"


def _app_declined() -> bool:
    try:
        return _JSON_OBJECT.validate_json(_start_state().read_text(encoding="utf-8")).get("app") == "declined"
    except (OSError, ValidationError):
        return False


def _remember_app_decline() -> None:
    path = _start_state()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"app": "declined"}), encoding="utf-8")
    except OSError:
        return  # A decline that cannot be remembered only means the question returns.


def _app_choice(arguments: list[str], flags: set[str], *, interactive: bool) -> bool:
    """Whether to install and open the desktop app: asked first, only on Apple silicon, declines remembered."""
    if arguments or "--no-app" in flags:
        _event("start.app.decision", answer=False, source="flag" if not arguments else "arguments")
        return False
    if not _supports_app():
        _event("start.app.decision", answer=False, source="platform")
        if interactive:
            _human_console().print(words.APP_UNSUPPORTED, style="dim", markup=False)
        return False  # Elsewhere the web app opens instead.
    if "--yes" in flags or not interactive:
        _event("start.app.decision", answer="--yes" in flags, source="flag" if "--yes" in flags else "non_interactive")
        return "--yes" in flags
    from heinzel.heads._desktop_installation import desktop_location  # noqa: PLC0415 - macOS app question only

    if "--ask" not in flags and (desktop_location() is not None or _app_declined()):
        installed = desktop_location() is not None
        _event("start.app.decision", answer=installed, source="installed" if installed else "remembered_decline")
        return installed  # Installed: refresh and open without asking; declined: stay quiet.
    from heinzel.heads.cli._setup import ConsolePrompter  # noqa: PLC0415 - same explicit consent boundary

    place = "Heinzelmännchen.app into /Applications, or ~/Applications without an administrator"
    answer = ConsolePrompter(_human_console()).confirm(words.APP_QUESTION.format(location=place), default=True)
    _event("start.app.decision", answer=answer, source="question")
    if answer:
        return True
    _remember_app_decline()
    return False


def _install_app(executable: Path, environment: dict[str, str]) -> bool:
    """Run the app installer with its own progress on the terminal, then require that macOS will open the app."""
    started = time.monotonic()
    _event("start.app_install", step="app_install", phase="start")
    command = [str(executable.parent / "python"), "-I", "-m", "heinzel", "app"]
    try:
        installed = subprocess.run(  # noqa: S603 - verified persistent interpreter and fixed operation
            [*command, "install", "--yes"], env=environment, check=False, timeout=900, stdout=sys.stderr
        )
        checked = subprocess.run(  # noqa: S603 - same interpreter, read-only check
            [*command, "doctor", "--json"], env=environment, capture_output=True, text=True, check=False, timeout=120
        )
        opens = (
            not installed.returncode and not checked.returncode and _json_object(checked.stdout).get("opens") is True
        )
    except (OSError, subprocess.SubprocessError, HeinzelError):
        opens = False
    _event(
        "start.app_install",
        phase="end",
        step="app_install",
        outcome="ok" if opens else "error",
        duration_ms=round((time.monotonic() - started) * 1000),
    )
    if not opens:
        sys.stderr.write(words.APP_DOWNLOAD_FAILED + "\n")
    return opens


def _json_object(raw: str) -> dict[str, JsonValue]:
    """Validate external JSON without echoing profile values or service output in an error.

    Raises:
        HeinzelError: If the managed command returns malformed JSON or a non-object result.
    """
    try:
        return _JSON_OBJECT.validate_json(raw, strict=True)
    except ValidationError as exc:
        msg = "The managed app returned invalid JSON; inspect its command before continuing setup."
        raise HeinzelError(msg) from exc


def _object_rows(value: JsonValue) -> list[dict[str, JsonValue]]:
    try:
        return _JSON_OBJECTS.validate_python(value, strict=True)
    except ValidationError as exc:
        msg = "The profile settings could not be verified; inspect `hz system config show`."
        raise HeinzelError(msg) from exc


def _setting_rows(value: JsonValue) -> list[dict[str, JsonValue]]:
    rows = _object_rows(value)
    identities: set[tuple[str, str]] = set()
    for row in rows:
        section, key, source = (row.get(name) for name in ("section", "key", "source"))
        if (
            not isinstance(section, str)
            or not section.strip()
            or not isinstance(key, str)
            or not key.strip()
            or not isinstance(source, str)
            or not source.strip()
            or (section, key) in identities
        ):
            msg = (
                "The configuration field identities and sources could not be verified; inspect `hz system config show`."
            )
            raise HeinzelError(msg)
        identities.add((section, key))
    return rows


def _missing_fields(profile: dict[str, JsonValue]) -> list[str]:
    try:
        return _STRINGS.validate_python(profile.get("missing"), strict=True)
    except ValidationError as exc:
        msg = "The profile declared invalid requirements; inspect `hz org show`."
        raise HeinzelError(msg) from exc


def _profile_choices(schema: JsonValue) -> list[str]:
    if not isinstance(schema, dict):
        msg = "The profile field schema could not be verified; inspect `hz system config show`."
        raise HeinzelError(msg)
    variants = [schema]
    if "anyOf" in schema:
        variants.extend(_object_rows(schema["anyOf"]))
    for variant in variants:
        if "enum" in variant:
            try:
                return _STRINGS.validate_python(variant["enum"], strict=True)
            except ValidationError as exc:
                msg = "The profile choices could not be verified; inspect `hz system config show`."
                raise HeinzelError(msg) from exc
    return []


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


def _newer_profile(executable: Path) -> install.AppProfile | None:
    """Resolve a newer stable composition for an already verified app, when reachable.

    Raises:
        HeinzelError: If the installed Core or published release metadata cannot be verified.
        ValueError: If the installed Core version is invalid.
    """
    if os.environ.get("HZ_APP_PROFILE"):
        return None  # An explicit installer selector never opts a retained app into floating upgrades.
    installation = Installation(Progress(), prefix=executable.parent.parent, uv=install.uv_executable())
    current = Version(installation.installed_core_version())
    try:
        profile = install.latest_profile()
    except install.ReleaseUnavailableError:
        sys.stderr.write(f"Could not check for a newer Heinzel. Opening verified installed version {current}.\n")
        return None
    if Version(profile.core_version) <= current:
        return None
    requirements = install.existing_requirements(installation.prefix)
    if conflicts := install.retained_plugin_conflicts(profile, installation, requirements):
        install.report_retained_plugins(profile.core_version, conflicts)
        return None
    environment = _app_environment(executable) | {"HZ_UPDATE_CHECK": "false", "HZ_PLUGINS_ENABLED": "false"}
    daemon = _managed_json(executable, environment, ["system", "daemon", "status"])
    if all(daemon.get(key) is True for key in ("installed", "owned", "loaded")):
        sys.stderr.write(
            f"Heinzel {profile.core_version} is available. Opening verified installed version {current} "
            "because its scheduled daemon is loaded. Run `hz system daemon status` for details.\n"
        )
        return None
    return profile


def _human_console() -> "Console":
    """Render setup on stderr with the terminal's default foreground."""
    from rich.console import Console  # noqa: PLC0415 - interactive presentation only

    return Console(file=sys.stderr)


def _welcome() -> None:
    from heinzel.heads.cli import _playful  # noqa: PLC0415 - only the bare interactive launcher
    from heinzel.heads.cli._branding import TAGLINE  # noqa: PLC0415 - reuse the CLI's shared wording

    console = _human_console()
    if _playful.allowed(Config.load(Path.home()).settings(Settings, "hz"), console):
        console.print(
            _playful.banner([f"Heinzelmännchen {__version__}", TAGLINE, "Let's set up your Heinzel."], body_style=""),
        )


def _interactive_launch(arguments: list[str]) -> bool:
    return bool(
        not arguments
        and sys.stdin.isatty()
        and sys.stdout.isatty()
        and Config.load(Path.home()).settings(Settings, "hz").output_format != "json"
    )


def _service_choices(arguments: list[str]) -> list[tuple[str, ...]]:
    if not _interactive_launch(arguments):
        return []
    _welcome()
    from heinzel.heads.cli._setup import ConsolePrompter  # noqa: PLC0415 - reuse the CLI's explicit consent boundary

    prompter = ConsolePrompter(_human_console())
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
        _event("start.service", step=" ".join(service), phase="start")
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
        _event("start.service", step=" ".join(service), phase="end", outcome="error" if installed.returncode else "ok")
        if installed.returncode:
            msg = f"Service installation did not complete. Inspect `hz {' '.join(service)} status` before retrying."
            raise HeinzelError(msg)
        if service == ("system", "notify"):
            result = _json_object(installed.stdout)
            _notifier_result(result)
            if installed_app() != executable:
                msg = "The managed installation changed during notifier setup; inspect it before launching."
                raise HeinzelError(msg)
            continue
        status = _service_status(executable, service, environment)
        if service == ("system", "daemon"):
            if status.get("owned") is not True:
                msg = "The daemon ownership and selected profile could not be verified; inspect its status."
                raise HeinzelError(msg)
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


def _service_status(executable: Path, service: tuple[str, ...], environment: dict[str, str]) -> dict[str, JsonValue]:
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
        status = _json_object(observed.stdout) if observed.returncode == 0 else {}
        if status.get("installed") is not True or status.get("loaded") is not True:
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
    try:
        result = _JSON_OBJECT.validate_python(result, strict=True)
    except ValidationError as exc:
        msg = "Notifier setup returned no verifiable result. Inspect `hz system notify list --json`."
        raise HeinzelError(msg) from exc
    if "notifier" not in result or type(result.get("shown")) is not bool:
        msg = "Notifier setup returned no verifiable result. Inspect `hz system notify list --json`."
        raise HeinzelError(msg)
    notifier = result.get("notifier")
    if notifier is None:
        sys.stderr.write(
            "Native notifications are unavailable on this platform or desktop. "
            "Run `hz system notify install` for guidance.\n"
        )
        return
    if notifier == "heinzel-notifier" and result.get("state") != "current" and result.get("state") != "installed":
        msg = "The notifier companion could not be verified. Inspect `hz system notify install` before retrying."
        raise HeinzelError(msg)
    if not isinstance(notifier, str) or notifier not in {"heinzel-notifier", "notify-send", "osascript"}:
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


def _has_command(children: list[JsonValue], group: str, command: str) -> bool:
    for item in children:
        subcommands = item.get("commands") if isinstance(item, dict) and item.get("command") == group else None
        if isinstance(subcommands, list):
            return any(isinstance(sub, dict) and sub.get("command") == command for sub in subcommands)
    return False


def _command_discovery_diagnostic(returncode: int, stderr: str) -> str:
    """Classify bounded stderr without returning any child-controlled text.

    Categories are hints, not trusted evidence: the child can print a misleading marker.
    Unknown exception names, paths, arguments and exception messages are never copied.
    """
    tail = stderr[-16384:]
    lines = tail.splitlines()[-64:]
    category = "other"
    for line in reversed(lines):
        if line.startswith(("ModuleNotFoundError:", "ImportError:")):
            category = "import-module"
            break
        if line.startswith(("FileNotFoundError:", "PermissionError:", "OSError:")):
            category = "launch-os"
            break
        if line.startswith(("SyntaxError:", "IndentationError:")):
            category = "syntax"
            break
        if line.startswith(("TypeError:", "AttributeError:", "ValueError:", "RuntimeError:")):
            category = "runtime"
            break
        if line.startswith(("usage:", "Usage:")):
            category = "usage"
            break
        if line.endswith(": No module named heinzel"):
            category = "import-module"
            break
    exit_class = "signal" if returncode < 0 else "nonzero"
    return f"command-discovery category={category} exit={exit_class}"


def _workspace_command(
    executable: Path,
    environment: dict[str, str],
    choices: list[tuple[str, ...]],
    app: bool = False,
) -> list[str]:
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
        diagnostic = _command_discovery_diagnostic(discovery.returncode, discovery.stderr)
        msg = f"{msg} [{diagnostic}]"
        raise HeinzelError(msg)
    tree = _json_object(discovery.stdout)
    root = tree.get("root")
    if type(tree.get("schema_version")) is not int or tree.get("schema_version") != 1 or not isinstance(root, dict):
        msg = "The installed app returned an unknown command schema; inspect it before launching."
        raise HeinzelError(msg)
    children = root.get("commands", [])
    if not isinstance(children, list):
        msg = "The installed app returned invalid command discovery; inspect it before launching."
        raise HeinzelError(msg)
    if app and _has_command(children, "hz app", "hz app open"):
        return _APP_WORKSPACE
    ui = next((item for item in children if isinstance(item, dict) and item.get("command") == "hz ui"), None)
    subcommands = ui.get("commands") if ui is not None else None
    if not isinstance(subcommands, list):
        msg = "The installed app's web commands are unavailable; inspect `hz --command-tree`."
        raise HeinzelError(msg)
    commands = {name for item in subcommands if isinstance(item, dict) and isinstance(name := item.get("command"), str)}
    if {"hz ui open", "hz ui serve"}.issubset(commands):
        return ["ui", "open"]
    if choices:
        msg = "The installed standard app predates these service choices. Update it before installing services."
        raise HeinzelError(msg)
    return ["ui"]  # A verified older standard app retains its published foreground entry point.


def _managed_command(executable: Path, environment: dict[str, str], arguments: list[str]) -> str:
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
    return result.stdout


def _managed_json(executable: Path, environment: dict[str, str], arguments: list[str]) -> dict[str, JsonValue]:
    """Object-returning reads retain their strict object-only response contract."""
    return _json_object(_managed_command(executable, environment, arguments))


def _write_setting(executable: Path, environment: dict[str, str], key: str, value: str) -> None:
    """Require the config setter's exact user-file path and independent effective-value readback.

    Raises:
        HeinzelError: If the path result, selected file or persisted setting cannot be verified.
    """
    report = _managed_json(executable, environment, ["system", "config", "show"])
    files = _object_rows(report.get("files"))
    user = [item.get("path") for item in files if item.get("scope") == "user"]
    if (
        len(user) != 1
        or not isinstance(user[0], str)
        or not Path(user[0]).is_absolute()
        or Path(user[0]).name != FILENAME
        or ".." in Path(user[0]).parts
    ):
        msg = "The user configuration file could not be verified; inspect `hz system config show`."
        raise HeinzelError(msg)
    raw = _managed_command(executable, environment, ["system", "config", "set", key, json.dumps(value)])
    try:
        path = _JSON_STRING.validate_json(raw, strict=True)
    except ValidationError as exc:
        msg = "The configuration command returned an invalid file result; inspect `hz system config show`."
        raise HeinzelError(msg) from exc
    if path != user[0]:
        msg = "The configuration command changed an unexpected file; inspect `hz system config show`."
        raise HeinzelError(msg)
    observed = _managed_json(executable, environment, ["system", "config", "get", key])
    row = _setting_rows([observed])[0]
    if (
        f"{row['section']}.{row['key']}" != key
        or row["source"] != "user"
        or not isinstance(row.get("value"), str)
        or row["value"] != value
    ):
        msg = "The saved setting could not be confirmed; inspect `hz system config show` before continuing."
        raise HeinzelError(msg)


def _profile_questions(
    executable: Path, environment: dict[str, str], *, confirm_names: bool = False
) -> list[tuple[str, list[str], str | None]]:
    profile = _managed_json(executable, environment, ["org", "show"])
    report = _managed_json(executable, environment, ["system", "config", "show"])
    settings = _setting_rows(report.get("settings"))
    missing = _missing_fields(profile)
    questions: list[tuple[str, list[str], str | None]] = []
    names = ("org.name", "team.name")
    for key in dict.fromkeys([*names, *missing]):
        if not key.startswith(("org.", "team.")):
            msg = "The profile declared unexpected requirements; inspect `hz org show`."
            raise HeinzelError(msg)
        field = next((item for item in settings if f"{item.get('section')}.{item.get('key')}" == key), None)
        if field is None or "value" not in field:
            msg = f"The required profile setting {key} is unavailable; inspect configuration before continuing."
            raise HeinzelError(msg)
        choices = _profile_choices(field.get("json_schema"))
        value = field["value"]
        if value is not None and not isinstance(value, str):
            msg = f"The required profile setting {key} is invalid; inspect configuration before continuing."
            raise HeinzelError(msg)
        if key in missing or value is None or not value.strip():
            questions.append((key, choices, None))
        elif confirm_names and key in names:
            questions.append((key, choices, value))
    return questions


def _complete_profile(executable: Path, environment: dict[str, str], prompter: Prompter | None = None) -> None:
    if prompter is None:
        from heinzel.heads.cli._setup import ConsolePrompter  # noqa: PLC0415 - same terminal boundary as service consent

        prompter = ConsolePrompter(_human_console())
    prompter.say("Your profile stays on this computer. Press Enter to keep a saved value.")
    for key, choices, default in _profile_questions(executable, environment, confirm_names=True):
        title = {
            "org.name": "Your name",
            "team.name": "Team name",
            "org.handle": "Handle",
            "org.role": "Your role",
            "org.timezone": "Time zone (IANA, e.g. Europe/Berlin)",
        }.get(key, key)
        answer = (
            prompter.choice(title, choices, default=default)
            if choices
            else prompter.text(
                title,
                default=default,
                validate=lambda value: None if value.strip() else "Enter a real value to complete your profile.",
            )
        )
        if answer != default:
            _write_setting(executable, environment, key, answer)
    prompter.say("Profile confirmed.")


def _require_profile(executable: Path, environment: dict[str, str]) -> None:
    if _profile_questions(executable, environment):
        msg = "Setup is incomplete: run `uvx heinzel@latest` in an interactive terminal to complete your real profile."
        raise HeinzelError(msg)


def _configure_ai(executable: Path, environment: dict[str, str], prompter: Prompter | None = None) -> None:
    report = _managed_json(executable, environment, ["system", "config", "show"])
    settings = _setting_rows(report.get("settings"))
    undecided = [
        key
        for item in settings
        if item.get("section") == "capabilities"
        and isinstance(key := item.get("key"), str)
        and key in {"ai_access", "agent_runner"}
        and item.get("source") == "default"
    ]
    if not undecided:
        return
    if prompter is None:
        from heinzel.heads.cli._setup import ConsolePrompter  # noqa: PLC0415 - no silent noninteractive selection

        prompter = ConsolePrompter(_human_console())
    if not prompter.confirm("Set up AI later? Existing explicit AI selections will be kept.", default=True):
        return
    for key in undecided:
        _write_setting(executable, environment, f"capabilities.{key}", "none")
    prompter.say("AI is intentionally not configured for deferred uses. Choose access when you need it.")


def _verify_setup(executable: Path, environment: dict[str, str]) -> None:
    health = _managed_json(executable, environment, ["system", "doctor"])
    if health.get("status") != "up":
        msg = "Setup is not complete: system health is not green. Run `hz system doctor` and resolve its real findings."
        raise HeinzelError(msg)
    sys.stderr.write("Setup complete; system health is up.\n")


def _prepare_profile(executable: Path, environment: dict[str, str], workspace: list[str], *, interactive: bool) -> None:
    if workspace != ["ui", "open"]:
        return  # Published older applications keep their existing prompt-free compatibility path.
    if interactive:
        _complete_profile(executable, environment)
        _configure_ai(executable, environment)
    else:
        _require_profile(executable, environment)


def _launch_app(executable: Path, workspace: list[str], environment: dict[str, str]) -> None:
    if installed_app() != executable:
        msg = "The managed app changed before launch; inspect it before continuing."
        raise HeinzelError(msg)
    interpreter = str(executable.parent / "python")
    os.execve(  # noqa: S606 - selected managed interpreter and isolated module; no shell
        interpreter, [interpreter, "-I", "-m", "heinzel", *workspace], environment
    )


def _app_environment(executable: Path) -> dict[str, str]:
    """Keep the verified interpreter first and keep installer inputs out of runtime commands."""
    # The installer has consumed the app selector; it is not a runtime or service setting.
    environment = {
        key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME", "HZ_APP_PROFILE"}
    }
    environment["PATH"] = f"{executable.parent}{os.pathsep}{os.environ.get('PATH', '')}"
    return environment


def _ensure_app(*, interactive: bool) -> Path:
    """Install or refresh the standard app before its workspace or profile is opened."""
    executable = installed_app()
    if executable is None:
        sys.stderr.write("Installing Heinzel and its default plugins…\n")
        status = install.main([])
        if status:
            sys.exit(status)
        executable = installed_app()
    elif interactive and (profile := _newer_profile(executable)) is not None:
        sys.stderr.write(f"Updating Heinzel to {profile.core_version}…\n")
        status = install.main([], newer_profile=profile)
        if status:
            sys.exit(status)
        executable = installed_app()
    if executable is None:
        sys.stderr.write("Installation did not leave a usable standard app. Retry `uvx heinzel@latest`.\n")
        sys.exit(1)
    return executable


def _start(arguments: list[str], flags: set[str]) -> None:
    interactive = _interactive_launch(arguments)
    _event("start.begin", interactive=interactive, flags=sorted(flags), version=__version__)
    unattended = not arguments and "--yes" in flags
    want_app = _app_choice(arguments, flags, interactive=interactive)
    choices = [("system", "daemon"), ("ui",), ("system", "notify")] if unattended else _service_choices(arguments)
    # Without a terminal and without --yes the desktop app is neither asked about nor installed (as --no-app), and
    # the foreground app starts exactly as before: `curl | sh` and scripts reach a running app.
    executable = _ensure_app(interactive=interactive)
    environment = _app_environment(executable)
    app_ready = want_app and _install_app(executable, environment)
    workspace = arguments or _workspace_command(executable, environment, choices, app_ready)
    if not arguments and not unattended:
        _prepare_profile(executable, environment, workspace, interactive=interactive)
    _install_services(executable, choices, environment)
    if interactive and workspace == ["ui", "open"]:
        _verify_setup(executable, environment)
    if not arguments:
        _event(
            "start.outcome",
            outcome="running",
            workspace=" ".join(workspace),
            opened=sys.stdout.isatty() or not unattended,
        )
        if unattended and not sys.stdout.isatty():
            sys.stderr.write(words.RUNNING_BACKGROUND + "\n")
            return  # An open from a script or ssh would pop up on someone's desk.
        sys.stderr.write((words.RUNNING_APP if workspace == _APP_WORKSPACE else words.RUNNING_WEB) + "\n")
    _launch_app(executable, workspace, environment)


def main() -> None:
    """Install the verified standard app, offer separate interactive services, and open its workspace."""
    arguments, flags = _start_options(sys.argv[1:])
    if any(argument in {"--help", "-h", "--version", "-V", "--command-tree"} for argument in arguments):
        from heinzel.heads.cli import main as cli_main  # noqa: PLC0415 - argument-driven CLI remains independent

        cli_main()
        return
    try:
        _start(arguments, flags)
    except KeyboardInterrupt:
        _event("start.outcome", outcome="interrupted")
        sys.stderr.write(words.INTERRUPTED + "\n")
        sys.exit(130)
    except HeinzelError as error:
        _event("start.outcome", outcome="error", error_type=type(error).__name__)
        sys.stderr.write(f"Heinzel could not start. {error}\n")
        sys.exit(1)
    except (OSError, ValueError, ValidationError, subprocess.SubprocessError):
        sys.stderr.write(
            "Heinzel could not start. Check your uv installation and connection, then retry `uvx heinzel@latest`.\n"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
