"""Drafting a person's reporting lines for setup to confirm: the seam between the `directory` capability and setup.

The maintainer's decision (ADR 0024, ADR 0028): the org chart is the `directory` capability. When a plugin can read
it (an HR system, a chat tool's profile fields, an IdP), hz uses it and the user only confirms; otherwise setup asks,
starting from a draft guessed from signals the user can already read, so the user corrects rather than types.

`draft_reporting_lines` is what setup and roster code call with the `Directory` handle they declared
(`requires=["directory:read"]`). It merges, per attribute (manager, reports), in this order: what a directory of
record says, then what the user confirmed earlier, then guesses. Every guess stays marked (`Relation.guess`) with its
evidence; nothing here stores anything, so a guess becomes a fact only when the caller stores what the user confirmed.
"""

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

    Per attribute, a directory of record wins over what the user confirmed (ADR 0033: the IdP overrides what people
    declare), and what the user confirmed wins over a guess. A directory that fails with a `HeinzelError` (for the
    `one-on-ones` adapter: no calendar bound) leaves the confirmed lines and says why in `problem`; setup then asks.

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
            manager=_confirmed(None, confirmed.manager) if confirmed.manager else None,
            reports=[_confirmed(None, report) for report in confirmed.reports or []],
            problem=str(exc),
        )
    manager = found.manager if found else None
    reports = list(found.reports) if found else []
    if confirmed.manager is not None and (manager is None or manager.guess):
        manager = _confirmed(directory, confirmed.manager)
    if confirmed.reports is not None and all(line.guess for line in reports):
        reports = [_confirmed(directory, report) for report in confirmed.reports]
    return ReportingDraft(person=person, found=found, manager=manager, reports=reports)


def _confirmed(directory: Directory | None, ident: str) -> Relation:
    """A confirmed line to `ident`, named as the directory names them when it knows them."""
    known: Person | None = None
    if directory is not None:
        try:
            known = directory.person(ident)
        except HeinzelError:
            known = None
    if known is not None:
        return Relation(who=known.who, login=known.login)
    if "@" in ident:
        return Relation(who=Participant(email=ident))
    return Relation(who=Participant(), login=ident)
