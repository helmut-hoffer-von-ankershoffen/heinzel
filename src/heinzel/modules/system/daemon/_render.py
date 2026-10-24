"""How the CLI head presents daemon results."""

from rich.console import Group, RenderableType
from rich.markup import escape
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from heinzel.modules.system.daemon._models import Disposition, Installed, JobInfo, LogLine, TickReport

MARKS = {
    Disposition.RENDERED: "[green]rendered[/green]",
    Disposition.SKIPPED: "[dim]skipped[/dim]",
    Disposition.FAILED: "[red]failed[/red]",
    None: "[yellow]due[/yellow]",
}


def render_installed(result: Installed) -> RenderableType:
    """The unit files, highlighted; with `--dry-run` that is all that happens."""
    parts: list[RenderableType] = []
    for path, content in result.files.items():
        parts.append(Text(f"# {path}", style="bold"))
        parts.append(
            Syntax(content, "xml" if path.suffix == ".plist" else "ini", background_color="default", word_wrap=True)
        )
    verb = "Would run" if result.dry_run else "Installed"
    parts.append(Text(f"{verb} {result.executable} system daemon tick every {result.interval_minutes} minutes."))
    return Group(*parts)


def render_tick(report: TickReport) -> RenderableType:
    """One line per job run, or why nothing ran."""
    if report.paused:
        return Text("Paused: no job ran. `hz system daemon resume` turns ticks back on.", style="yellow")
    if report.locked:
        return Text("Another tick is running; this one did nothing.", style="yellow")
    if not report.runs:
        return Text("Nothing was due.")
    table = Table(title="Dry run: due jobs" if report.dry_run else "Tick")
    for column in ("Job", "Slot", "Wake", "Outcome", "Detail"):
        table.add_column(column, overflow="fold")
    for run in report.runs:
        table.add_row(
            escape(run.job), run.as_of.isoformat(), run.wake.value, MARKS[run.disposition], escape(run.error or "")
        )
    return table


def render_jobs(jobs: list[JobInfo]) -> RenderableType:
    """A table of jobs with their schedule, next slot and last run."""
    if not jobs:
        return Text("No module advertises a job.")
    table = Table(title="Jobs")
    for column in ("Job", "Schedule", "Next", "Last", "Summary"):
        table.add_column(column, overflow="fold")
    for job in jobs:
        last = "never" if job.last_disposition is None else f"{MARKS[job.last_disposition]} {job.last_as_of}"
        upcoming = f"[red]{escape(job.problem)}[/red]" if job.problem else str(job.next_run)
        table.add_row(escape(job.job), escape(job.schedule), upcoming, last, escape(job.summary))
    return table


def render_logs(lines: list[LogLine]) -> RenderableType:
    """Log lines, one per row."""
    table = Table(show_header=False, box=None)
    for line in lines:
        style = {"ERROR": "red", "WARNING": "yellow"}.get(line.level, "")
        table.add_row(line.at.isoformat(timespec="seconds"), Text(line.level, style=style), escape(line.message))
    return table
