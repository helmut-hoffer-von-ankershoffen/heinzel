"""How the CLI head presents configuration."""

import json

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from heinzel.modules.system.config._models import ConfigReport


def render_report(report: ConfigReport) -> RenderableType:
    """The files in precedence order, then the settings with their overriding environment variables."""
    files = Table(title="Configuration files (lowest precedence first; environment variables override all)")
    for column in ("Scope", "Path", "Exists"):
        files.add_column(column, overflow="fold")
    for file in report.files:
        files.add_row(file.scope, Text(str(file.path)), "yes" if file.exists else "[dim]no[/dim]")

    settings = Table(title="Settings in effect")
    for column in ("Section", "Key", "Value", "Environment variable", "Description"):
        settings.add_column(column, overflow="fold")
    for setting in report.settings:
        value = setting.value if isinstance(setting.value, str) else json.dumps(setting.value)
        settings.add_row(
            Text(setting.section), Text(setting.key), Text(value), Text(setting.env), Text(setting.description)
        )
    return Group(files, settings)
