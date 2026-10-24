"""The doctor submodule of system: every module's health checks in one report, for people, agents and CI."""

import os
import shutil
import sys
from pathlib import Path

from pydantic import BaseModel, Field
from rich.console import RenderableType
from rich.table import Table
from rich.text import Text

from heinzel import Check, HealthLet, Kernel, Module, Settings, Status, __version__
from heinzel.kernel import ReleaseCache

MINIMUM_PYTHON = (3, 12)

module = Module("doctor", "Check that Heinzelmännchen and its modules are healthy.")


class HealthReport(BaseModel):
    """All health checks, and the overall status: DOWN beats DEGRADED beats UP."""

    status: Status = Field(description="Overall status: the worst of all checks.")
    checks: dict[str, list[Check]] = Field(description="Checks by qualified module name.")


ORDER = [Status.UP, Status.DEGRADED, Status.DOWN]


def _render(report: HealthReport) -> RenderableType:
    marks = {
        Status.UP: "[green]✔ up[/green]",
        Status.DEGRADED: "[yellow]▲ degraded[/yellow]",
        Status.DOWN: "[red]✖ down[/red]",
    }
    table = Table(title=f"Heinzelmännchen is {marks[report.status]}")
    for column in ("Module", "Check", "Status", "Detail", "Fix"):
        table.add_column(column, overflow="fold")
    for qualname, checks in report.checks.items():
        for check in checks:
            # Text, not markup: details and fixes quote configuration, e.g. `[capabilities] code_host = ...`.
            table.add_row(Text(qualname), Text(check.name), marks[check.status], Text(check.detail), Text(check.fix))
    return table


def _exit_code(report: HealthReport) -> int:
    return 1 if report.status is Status.DOWN else 0


@module.operation(render=_render, exit_code=_exit_code, audience="everyday")
def doctor(kernel: Kernel) -> HealthReport:
    """Run every module's health checks; exits with 1 if anything is down."""
    checks: dict[str, list[Check]] = {}
    for record, let in kernel.lets(HealthLet):
        if let.live:  # reaches the network; `hz system connector test` runs it on demand (ADR 0056)
            continue
        try:
            checks[record.qualname] = kernel.call(let.provider)
        except Exception as exc:  # noqa: BLE001 - a broken check is a finding, not a crash
            checks[record.qualname] = [Check(name="checks", status=Status.DOWN, detail=f"{type(exc).__name__}: {exc}")]
    statuses = [check.status for group in checks.values() for check in group]
    return HealthReport(status=max(statuses, key=ORDER.index, default=Status.UP), checks=checks)


@module.health
def core(kernel: Kernel, settings: Settings) -> list[Check]:
    """Heinzelmännchen's own checks: Python, uv, the installation, plugins and its directories."""
    checks = [
        Check(name="python", status=Status.UP, detail=sys.version.split()[0])
        if sys.version_info >= MINIMUM_PYTHON
        else Check(name="python", status=Status.DOWN, detail=f"{sys.version.split()[0]} is too old", fix="Use 3.12+"),
    ]
    uv = shutil.which("uv")
    checks.append(
        Check(name="uv", status=Status.UP, detail=uv)
        if uv
        else Check(
            name="uv",
            status=Status.DEGRADED,
            detail="uv is not on PATH; plugins and updates can't be managed",
            fix="Install uv: https://docs.astral.sh/uv/",
        )
    )
    receipt = Path(sys.prefix) / "uv-receipt.toml"
    checks.append(
        Check(name="installation", status=Status.UP, detail=f"uv tool environment {sys.prefix}")
        if receipt.is_file()
        else Check(
            name="installation",
            status=Status.DEGRADED,
            detail=f"not a uv tool environment ({sys.prefix}); `hz system update` is unavailable",
            fix="Install with `uv tool install git+https://github.com/helmut-hoffer-von-ankershoffen/heinzel`",
        )
    )
    checks.extend(
        Check(name=f"plugin {name}", status=Status.DEGRADED, detail=error, fix=f"Reinstall or remove the plugin {name}")
        for name, error in kernel.errors.items()
    )
    # A repository someone else controls must not decide what runs or where data goes (ADR 0028): settings that
    # do are ignored in a project hz.toml. Name the key, never the value.
    project = next((file.path for file in kernel.config.files if file.scope == "project"), None)
    checks.extend(
        Check(
            name=f"project setting {section}.{key}",
            status=Status.DEGRADED,
            detail=f"{project} sets it, which only the user, site or HZ_CONFIG file may; it is ignored",
            fix=f"Remove it from {project}, or set it in your user configuration: hz system config set {section}.{key}",
        )
        for section, model in kernel.settings_of()
        for key in kernel.config.project_keys(section, type(model).ignored_in_project)
    )
    checks.append(release_check(settings))
    for name, directory in (("data_dir", settings.data_dir), ("cache_dir", settings.cache_dir)):
        existing = next((p for p in (directory, *directory.parents) if p.exists()), None)
        writable = existing is not None and os.access(existing, os.W_OK)
        checks.append(
            Check(name=name, status=Status.UP, detail=str(directory))
            if writable
            else Check(
                name=name, status=Status.DOWN, detail=f"{directory} is not writable", fix=f"Set HZ_{name.upper()}"
            )
        )
    return checks


def release_check(settings: Settings) -> Check:
    """Whether a newer release is known, from the release cache (ADR 0031); informational, so always up."""
    cache = ReleaseCache(settings.cache_dir)
    failed = f"; the last check failed: {cache.error}" if cache.error else ""
    if latest := cache.newer_than(__version__):
        return Check(
            name="release",
            status=Status.UP,
            detail=f"hz {latest} is available (installed {__version__}){failed}",
            fix="hz system update",
        )
    if cache.latest is not None:
        detail = f"hz {__version__} is up to date (latest release {cache.latest}){failed}"
    elif cache.error:
        detail = f"no release known yet; the last check failed: {cache.error}"
    else:
        detail = "no release known yet"
    if not settings.update_check:
        detail += "; the hourly check is off (update_check = false)"
    return Check(name="release", status=Status.UP, detail=detail, fix="" if cache.latest else "hz system release")


__all__ = ["HealthReport", "module"]
