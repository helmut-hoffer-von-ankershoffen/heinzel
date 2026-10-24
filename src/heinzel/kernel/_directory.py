"""Drafting a person's reporting lines for setup to confirm: the seam between the `directory` capability and setup.

The maintainer's decision (ADR 0024, ADR 0028): the org chart is the `directory` capability. When a plugin can read
it (an HR system, a chat tool's profile fields, an IdP), hz uses it and the user only confirms; otherwise setup asks,
starting from a draft guessed from signals the user can already read, so the user corrects rather than types.

`draft_reporting_lines` is what setup steps and roster drafting call with the `Directory` handle they declared
(`requires=["directory:read"]`). It merges, per attribute (manager, reports), in this order: what a directory of
record says, then what the user confirmed earlier, then guesses. Every guess stays marked (`Relation.guess`) with its
evidence; nothing here stores anything, so a guess becomes a fact only when the caller stores what the user confirmed.
"""

from collections.abc import Iterable
from typing import Self

from pydantic import BaseModel, Field, model_validator

from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._protocols import Directory, Participant, Person, Relation


class ConfirmedLines(BaseModel):
    """What the user confirmed about one person's reporting lines, as the caller stored it; logins or emails."""

    manager: str | None = Field(
        default=None, description="Login or email of the manager the user confirmed; none if not confirmed."
    )
    reports: list[str] | None = Field(
        default=None,
        description="Logins or emails of the direct reports the user confirmed; none if not confirmed, empty for "
        "confirmed none.",
    )


class ReportingDraft(BaseModel):
    """One person's reporting lines for setup to show: directory facts, confirmed lines and guesses, each marked."""

    person: str = Field(description="Whom the draft is about: the login or email it was asked for.")
    found: Person | None = Field(
        default=None, description="What the directory says about them (name, title, team, ...); none if unknown."
    )
    manager: Relation | None = Field(default=None, description="Their manager; none if nothing says who.")
    reports: list[Relation] = Field(default_factory=list[Relation], description="Their direct reports.")
    problem: str | None = Field(
        default=None, description="Why the directory couldn't be read, e.g. no calendar is bound; none if it could."
    )
    conflicts: list[str] = Field(
        default_factory=list,
        description="Where the directory of record and what the user confirmed disagree; the directory's line is "
        "kept, and setup shows these instead of dropping the confirmation silently.",
    )

    needs_confirmation: bool = Field(
        default=False,
        description="Whether any line is a guess the user still has to confirm or correct; derived, never passed.",
    )

    @model_validator(mode="after")
    def _derive_needs_confirmation(self) -> Self:
        self.needs_confirmation = any(line.guess for line in (*([self.manager] if self.manager else []), *self.reports))
        return self


def draft_reporting_lines(directory: Directory, person: str, confirmed: ConfirmedLines | None = None) -> ReportingDraft:
    """Draft `person`'s manager and direct reports for the user to confirm or correct.

    Per attribute, a directory of record wins over what the user confirmed (ADR 0024 §2: precedence per attribute;
    the directory of record decides existence and manager), and what the user confirmed wins over a guess. Where the
    directory and a confirmation disagree, the draft keeps the directory's line and says so in `conflicts`, never
    silently. A directory that fails with a `HeinzelError` (for the `one-on-ones` adapter: no calendar bound) leaves
    the confirmed lines and says why in `problem`; setup then asks.

    Args:
        directory: The `directory` handle the caller declared (`requires=["directory:read"]`).
        person: Login or email of whom to draft, usually the user.
        confirmed: What the user confirmed earlier, as the caller stored it; nothing if they haven't.
    """
    confirmed = confirmed or ConfirmedLines()
    try:
        found = directory.person(person)
    except HeinzelError as exc:
        return ReportingDraft(
            person=person,
            manager=_confirmed(None, (), confirmed.manager) if confirmed.manager else None,
            reports=[_confirmed(None, (), report) for report in confirmed.reports or []],
            problem=str(exc),
        )
    manager = found.manager if found else None
    reports = list(found.reports) if found else []
    known = [line for line in (manager, *reports) if line is not None]
    conflicts: list[str] = []
    if confirmed.manager is not None:
        if manager is None or manager.guess:
            manager = _confirmed(directory, known, confirmed.manager)
        elif not _names(manager, confirmed.manager):
            conflicts.append(f"manager: the directory says {_label(manager)}; you confirmed {confirmed.manager}")
    if confirmed.reports is not None:
        if all(line.guess for line in reports):
            reports = [_confirmed(directory, known, report) for report in confirmed.reports]
        elif not _same(reports, confirmed.reports):
            conflicts.append(
                f"reports: the directory says {', '.join(_label(line) for line in reports)}; you confirmed "
                f"{', '.join(confirmed.reports) or 'none'}"
            )
    return ReportingDraft(person=person, found=found, manager=manager, reports=reports, conflicts=conflicts)


def _names(line: Relation, ident: str) -> bool:
    """Whether `ident` (a login or email) is the person `line` names; emails ignore case."""
    wanted = ident.strip().casefold()
    return wanted in {(line.login or "").casefold(), (line.who.email or "").casefold()} - {""}


def _same(lines: list[Relation], idents: list[str]) -> bool:
    """Whether `idents` name exactly the people `lines` name."""
    return all(any(_names(line, i) for i in idents) for line in lines) and all(
        any(_names(line, i) for line in lines) for i in idents
    )


def _label(line: Relation) -> str:
    return line.who.name or line.login or "someone without a display name"


def _confirmed(directory: Directory | None, known: Iterable[Relation], ident: str) -> Relation:
    """A confirmed line to `ident`, named as the draft or the directory names them; looked up only if needed."""
    for line in known:
        if _names(line, ident):
            return Relation(who=line.who, login=line.login)
    person: Person | None = None
    if directory is not None:
        try:
            person = directory.person(ident)
        except HeinzelError:
            person = None
    if person is not None:
        return Relation(who=person.who, login=person.login)
    if "@" in ident:
        return Relation(who=Participant(email=ident))
    return Relation(who=Participant(), login=ident)
