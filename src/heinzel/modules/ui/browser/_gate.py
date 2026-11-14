"""The browser's commit gate: a pure function over what an input would hit (ADR 0162, safety statements 3 and 4).

The desktop describes the target of a click, of typing or of a key before anything happens. `decide` answers one of
three things from that description alone: the input runs; it is a commit, proposed to the person; or it is on the
never-list and refused. Nothing here reads the page, the model's words or a setting, and no tool result can change
the answer.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from heinzel.modules.ui.browser._words import COMMIT_WORDS, PHRASES, SECRET_FIELD_PHRASES, SECRET_FIELD_WORDS

Input = Literal["click", "type", "key"]
Outcome = Literal["run", "commit", "never"]

TAKE_OVER: Final = "ask the person to take over the tab"
_WORD = re.compile(r"[^\W\d_]+\d*", re.UNICODE)
_SECRET_AUTOCOMPLETE: Final = frozenset({"current-password", "new-password", "one-time-code"})
_ENTER: Final = frozenset({"enter", "return", "numpadenter"})
_ACTIVATE: Final = _ENTER | {"space", "spacebar"}
_HARMLESS_IN_SECRET: Final = frozenset({"tab", "escape", "esc"})
_MOVES: Final = frozenset({
    "tab", "escape", "esc", "pageup", "pagedown", "home", "end",
    "arrowup", "arrowdown", "arrowleft", "arrowright", "up", "down", "left", "right",
})  # fmt: skip
"""Keys that only move the focus or the view. Outside a text field every other key may be a shortcut of the site
(Delete removes a mail, `e` archives it), so it is a commit."""
_PLAIN_MODIFIERS: Final = frozenset({"shift"})


class Target(BaseModel):
    """What an input would hit, as the desktop describes it (bridge protocol v1); extra facts are ignored."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    resolved: bool
    host: str = ""
    role: str = ""
    name: str = ""
    tag: str = ""
    type: str = ""
    autocomplete: str = ""
    editable: bool = False
    submit: bool = False
    in_form: bool = False
    form_has_password: bool = False
    payment_page: bool = False
    never: str | bool | None = None
    """The desktop's own never-list verdict: empty, or which kind of field (`password`, `one-time-code`, ...)."""


@dataclass(frozen=True, slots=True)
class Verdict:
    """The gate's answer and, unless it runs, why."""

    outcome: Outcome
    reason: str = ""


def words(text: str) -> list[str]:
    """The words of a control's name, case-folded: `Jetzt Löschen!` is `jetzt`, `löschen`."""
    return _WORD.findall(unicodedata.normalize("NFKC", text).casefold())


def _holds(found: list[str], single: frozenset[str], phrases: tuple[tuple[str, ...], ...]) -> bool:
    if single.intersection(found):
        return True
    return any(found[i : i + len(phrase)] == list(phrase) for phrase in phrases for i in range(len(found)))


def commit_name(name: str) -> bool:
    """Whether a control's name holds a commit word (send, buy, delete, their German forms)."""
    return _holds(words(name), COMMIT_WORDS, PHRASES)


def secret_field(target: Target) -> bool:
    """Whether the target is a password, one-time-code or payment-card field: no agent types there."""
    if target.never or target.type.casefold() == "password":
        return True
    tokens = target.autocomplete.casefold().split()
    if any(token in _SECRET_AUTOCOMPLETE or token.startswith("cc-") for token in tokens):
        return True
    return target.editable and _holds(words(target.name), SECRET_FIELD_WORDS, SECRET_FIELD_PHRASES)


def chord_key(keys: str) -> str:
    """The key of one chord, case-folded: `ctrl+A` is `a`, `Enter` is `enter`."""
    return keys.rsplit("+", 1)[-1].strip().casefold()


def chord_commands(keys: str) -> bool:
    """Whether the chord holds ctrl, alt or meta: a command to the site or the browser, not a character."""
    held = {part.strip().casefold() for part in keys.split("+")[:-1]}
    return bool(held - _PLAIN_MODIFIERS)


def _shortcut(target: Target, keys: str, key: str) -> bool:
    """Whether a key outside a text field may be a shortcut of the site: anything but moving, Enter and Space."""
    if target.editable:
        return False
    return chord_commands(keys) or key not in _MOVES | _ACTIVATE


def _refused(action: Input, target: Target, key: str) -> str | None:
    """Why no agent does this input at all, or None."""
    if not target.resolved:
        if action == "click":
            return None
        return f"hz cannot tell which field this goes into; read the page and name a ref, or {TAKE_OVER}"
    if action != "click" and secret_field(target) and not (action == "key" and key in _HARMLESS_IN_SECRET):
        return f"this is a password, one-time-code or payment-card field; {TAKE_OVER}"
    return None


def _presses(action: Input, target: Target, key: str) -> bool:
    """Whether the input presses the target as a click does: a click, or Enter or Space on a focused control."""
    return action == "click" or (action == "key" and key in _ACTIVATE and not target.editable)


def _commit(action: Input, target: Target, text: str, keys: str) -> str | None:
    """Why this input is a commit, or None."""
    key = chord_key(keys) if action == "key" else ""
    form_field = target.editable or target.in_form
    reasons = (
        (not target.resolved, "the target of these coordinates cannot be told"),
        (target.payment_page, "the page has a payment field"),
        (action == "type" and "\n" in text and form_field, "a new line in a form field sends it like Enter"),
        (action == "key" and key in _ENTER and form_field, "Enter in a form field sends the form"),
        (
            action == "key" and _shortcut(target, keys, key),
            "outside a text field this key may be a shortcut of the site, such as delete or archive",
        ),
        (_presses(action, target, key) and target.submit, "it submits a form"),
        (
            _presses(action, target, key) and not target.editable and commit_name(target.name),
            "its name says it sends, buys, deletes or agrees",
        ),
    )
    return next((reason for applies, reason in reasons if applies), None)


def decide(action: Input, target: Target, *, text: str = "", keys: str = "") -> Verdict:
    """Whether one input runs, is a commit, or is never done by an agent.

    `text` is what `type` would type and `keys` the one chord `key` would press.
    """
    key = chord_key(keys) if action == "key" else ""
    if reason := _refused(action, target, key):
        return Verdict("never", reason)
    if reason := _commit(action, target, text, keys):
        return Verdict("commit", reason)
    return Verdict("run")
