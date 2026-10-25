"""Service of the claude submodule: what Claude has configured on this computer, read-only (ADR 0062)."""

from collections import Counter
from pathlib import Path

from heinzel import Check, Status
from heinzel.modules.system.claude._models import ClaudeScan, ClaudeSummary
from heinzel.modules.system.claude._scan import LINK, Scanner


class ClaudeService:
    """Scans Claude Code's and Claude Desktop's files in a home directory; switches nothing on, writes nothing."""

    def __init__(self, home: Path) -> None:
        """Scan `home`: tests pass a fake home directory."""
        self._home = home

    @classmethod
    def for_user(cls) -> "ClaudeService":
        """The service for your own home directory."""
        return cls(Path.home())

    def scan(self) -> ClaudeScan:
        """Everything found, with names only for environment variables and headers, never their values."""
        scanner = Scanner(self._home)
        scanner.scan()
        found = bool(scanner.servers or scanner.skills or scanner.connectors)
        return ClaudeScan(
            servers=scanner.servers,
            skills=scanner.skills,
            connectors=scanner.connectors,
            skipped=scanner.skipped,
            next=[] if found else ["Nothing found: Claude Code and Claude Desktop have no servers or skills here."],
        )

    def summary(self) -> ClaudeSummary:
        """Counts per source, without a single name."""
        scan = self.scan()
        return ClaudeSummary(
            servers=dict(Counter(s.source for s in scan.servers)),
            skills=dict(Counter(s.source for s in scan.skills)),
            connectors=scan.connectors,
            skipped=len(scan.skipped),
        )

    def checks(self) -> list[Check]:
        """Whether every Claude file could be read; counts only, so doctor output names nothing."""
        broken = [s for s in self.scan().skipped if s.reason != LINK]  # a refused link is listed, not a fault
        if broken:
            return [
                Check(
                    name="claude files",
                    status=Status.DEGRADED,
                    detail=f"{len(broken)} Claude file(s) could not be read: unreadable, invalid or too large",
                    fix="hz system claude list",
                )
            ]
        return [Check(name="claude files", status=Status.UP)]
