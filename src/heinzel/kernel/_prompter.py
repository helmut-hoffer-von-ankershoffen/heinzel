"""Questions to a person, in every head that has one: what setup steps ask through (ADR 0032).

Setup steps ask for `Prompter` by type. The questions, defaults, validation and re-asking live here, so every head
behaves the same; a head implements only `read` (one line of input) and `say` (one line of output). The kernel's
default has nobody to ask and refuses; the CLI binds a console prompter, tests a `ScriptedPrompter`.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from types import MappingProxyType
from typing import cast

from heinzel.kernel._errors import HeinzelError

Validate = Callable[[str], str | None]
"""Checks an answer: the problem to show before asking again, or None when it is fine."""

YES = frozenset({"y", "yes"})
NONE = "none"
"""The answer that picks none of several options."""
NO = frozenset({"n", "no"})


class Prompter:
    """Asks a person questions. This base implementation has nobody to ask: every question raises.

    A question may carry a `key`, its name within the asking module (`role` in `org` is `org.role`). Keyed questions
    are answered from `presets` when it has the key, so a setup can run without a terminal (`hz setup --answers`), and
    every keyed answer is kept in `answered`, so a finished setup can be written back as an answers file. Secrets are
    never keyed: they never come from or go to a file (ADR 0030).
    """

    interactive = False
    """Whether someone answers; heads without a person (MCP, REST, the daemon) keep the default."""

    presets: Mapping[str, object] = MappingProxyType({})
    """Answers given in advance, by full key (`org.role`): text, a number, true or false, or a list for `choices`."""
    scope = ""
    """The qualified name of the module asking, which keys are relative to; the setup runner sets it per step."""

    @property
    def answered(self) -> dict[str, object]:
        """Every keyed answer so far, by full key, as the question returned it."""
        return cast("dict[str, object]", self.__dict__.setdefault("_answered", {}))

    def read(self, prompt: str, *, secret: bool = False) -> str:
        """One answer to `prompt`, without echo when `secret`; heads override this.

        Raises:
            HeinzelError: Always: there is nobody to ask.
        """
        del secret
        msg = f"Can't ask {prompt.strip()!r}: nobody is there to answer; run setup where a person can."
        raise HeinzelError(msg)

    def say(self, text: str) -> None:
        """Tell the person something: a heading, the options of a choice, why an answer was refused."""
        del text

    def full_key(self, key: str) -> str:
        """`key` with the asking module's name in front: `role` asked by `org` is `org.role`."""
        return f"{self.scope}.{key}" if self.scope else key

    def text(
        self, question: str, *, default: str | None = None, validate: Validate | None = None, key: str | None = None
    ) -> str:
        """Free text; an empty answer takes `default`, and is asked again when there is none.

        Raises:
            HeinzelError: If nobody can answer, or a preset answer is invalid.
        """
        while True:
            answer = self._answer(_prompt(question, default), key).strip()
            if not answer:
                if default is not None:
                    return self._keep(key, default)
                self._refuse("Please answer; there is no default.", key)
                continue
            if validate is not None and (problem := validate(answer)):
                self._refuse(problem, key)
                continue
            return self._keep(key, answer)

    def secret(self, question: str) -> str:
        """A value that isn't echoed, such as a token; never empty, never preset, never kept.

        Raises:
            HeinzelError: If nobody can answer.
        """
        while not (answer := self.read(_prompt(question, None), secret=True)).strip():
            self.say("The value is empty; please enter it.")
        return answer.rstrip("\r\n")  # a token is taken as typed; only the line ending goes

    def confirm(self, question: str, *, default: bool = True, key: str | None = None) -> bool:
        """Yes or no; an empty answer takes `default`.

        Raises:
            HeinzelError: If nobody can answer, or a preset answer is invalid.
        """
        suffix = "[Y/n]" if default else "[y/N]"
        while True:
            answer = self._answer(f"{question} {suffix} ", key).strip().lower()
            if not answer:
                return self._keep(key, default)
            if answer in YES | NO:
                return self._keep(key, answer in YES)
            self._refuse("Please answer y or n.", key)

    def number(
        self,
        question: str,
        *,
        default: int | None = None,
        minimum: int | None = None,
        maximum: int | None = None,
        key: str | None = None,
    ) -> int:
        """A whole number between `minimum` and `maximum` (inclusive); an empty answer takes `default`.

        Raises:
            HeinzelError: If nobody can answer, or a preset answer is invalid.
        """
        bounds = _bounds(minimum, maximum)

        def check(answer: str) -> str | None:
            try:
                value = int(answer)
            except ValueError:
                return f"Please enter a whole number{bounds}."
            if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
                return f"Please enter a whole number{bounds}."
            return None

        value = int(self.text(question, default=None if default is None else str(default), validate=check, key=key))
        return self._keep(key, value)

    def choice(
        self, question: str, options: Sequence[str], *, default: str | None = None, key: str | None = None
    ) -> str:
        """One of `options`, by its number or its text (any case); an empty answer takes `default`.

        Raises:
            ValueError: If there are no options, or `default` isn't one of them.
            HeinzelError: If nobody can answer, or a preset answer is invalid.
        """
        _check_options(options, [] if default is None else [default])
        if not self._preset(key):
            self._list(options)
        answer = self.text(
            question,
            default=None if default is None else str(options.index(default) + 1),
            validate=lambda a: None if _pick(a, options) is not None else _unknown(a, options),
            key=key,
        )
        return self._keep(key, options[_index(answer, options)])

    def choices(
        self, question: str, options: Sequence[str], *, default: Iterable[str] = (), key: str | None = None
    ) -> list[str]:
        """Some of `options`, by numbers or texts separated by commas, or `none`; an empty answer takes `default`.

        Raises:
            ValueError: If there are no options, or a default isn't one of them.
            HeinzelError: If nobody can answer, or a preset answer is invalid.
        """
        defaults = list(default)
        _check_options(options, defaults)
        if not self._preset(key):
            self._list(options)

        def check(answer: str) -> str | None:
            unknown = [part for part in _parts(answer) if _pick(part, options) is None]
            return _unknown(unknown[0], options) if unknown else None

        answer = self.text(
            f"{question} (numbers separated by commas, or {NONE})",
            default=",".join(str(options.index(d) + 1) for d in defaults) if defaults else None,
            validate=check,
            key=key,
        )
        if answer.lower() == NONE:
            return self._keep(key, [])
        picked = [options[_index(part, options)] for part in _parts(answer)]
        return self._keep(key, list(dict.fromkeys(picked)))

    def _list(self, options: Sequence[str]) -> None:
        for number, option in enumerate(options, start=1):
            self.say(f"  {number}) {option}")

    def _preset(self, key: str | None) -> bool:
        return key is not None and self.full_key(key) in self.presets

    def _answer(self, prompt: str, key: str | None) -> str:
        """The preset answer for `key` as a person would type it, or one read from the person.

        Raises:
            HeinzelError: If there is no preset and nobody to ask, naming the key to add to the answers file.
        """
        self.__dict__["_from_preset"] = False
        if key is None:
            return self.read(prompt)
        full = self.full_key(key)
        if full in self.presets:
            self.__dict__["_from_preset"] = True
            return _typed(self.presets[full])
        if not self.interactive:
            section, _, name = full.rpartition(".")
            msg = (
                f"No answer for {full} ({prompt.strip().rstrip(':')}): add `{name} = ...` under [{section}] in the "
                "answers file, or run setup in a terminal."
            )
            raise HeinzelError(msg)
        return self.read(prompt)

    def _refuse(self, problem: str, key: str | None) -> None:
        """Explain why an answer was refused; a refused preset can't be asked again, so it fails.

        Raises:
            HeinzelError: If the refused answer was preset.
        """
        if key is not None and self.__dict__.get("_from_preset"):
            msg = f"The answer for {self.full_key(key)} is invalid: {problem}"
            raise HeinzelError(msg)
        self.say(problem)

    def _keep[T](self, key: str | None, value: T) -> T:
        if key is not None:
            self.answered[self.full_key(key)] = value
        return value


class ScriptExhausted(Exception):  # noqa: N818 - says what happened, like StopIteration
    """A `ScriptedPrompter` was asked more questions than it has answers: a test's script is too short."""


INTERRUPT = "\x03"
"""An answer for `ScriptedPrompter` that interrupts like Ctrl-C does."""


class ScriptedPrompter(Prompter):
    """Answers questions from a script, for tests: `ScriptedPrompter(["", "Tokyo", "1", INTERRUPT])`.

    Every prompt and every line said is recorded in `transcript`, prompts with the answer given.
    """

    interactive = True

    def __init__(self, answers: Iterable[str] = ()) -> None:
        """Answer with `answers`, in order; `INTERRUPT` raises `KeyboardInterrupt` instead."""
        self.answers = list(answers)
        self.transcript: list[str] = []

    def read(self, prompt: str, *, secret: bool = False) -> str:
        """The next scripted answer; a secret is recorded as `***`.

        Raises:
            KeyboardInterrupt: For the answer `INTERRUPT`.
            ScriptExhausted: When the script has no answer left, naming the prompt.
        """
        if not self.answers:
            msg = f"The script has no answer left for {prompt.strip()!r}"
            raise ScriptExhausted(msg)
        answer = self.answers.pop(0)
        if answer == INTERRUPT:
            self.transcript.append(f"{prompt}^C")
            raise KeyboardInterrupt
        self.transcript.append(prompt + ("***" if secret else answer))
        return answer

    def say(self, text: str) -> None:
        """Record what would have been shown."""
        self.transcript.append(text)


def _typed(value: object) -> str:
    """A preset answer as a person would type it: `y`/`n`, a number, text, or options separated by commas."""
    if isinstance(value, bool):
        return "y" if value else "n"
    if isinstance(value, list | tuple):
        items = [str(item) for item in cast("Sequence[object]", value)]
        return ",".join(items) if items else NONE
    return str(value)


def _prompt(question: str, default: str | None) -> str:
    return f"{question} [{default}]: " if default not in {None, ""} else f"{question}: "


def _bounds(minimum: int | None, maximum: int | None) -> str:
    if minimum is not None and maximum is not None:
        return f" from {minimum} to {maximum}"
    if minimum is not None:
        return f" of at least {minimum}"
    if maximum is not None:
        return f" of at most {maximum}"
    return ""


def _check_options(options: Sequence[str], defaults: Sequence[str]) -> None:
    if not options:
        msg = "A choice needs at least one option"
        raise ValueError(msg)
    if missing := [d for d in defaults if d not in options]:
        msg = f"Default {missing[0]!r} is not one of the options"
        raise ValueError(msg)


def _parts(answer: str) -> list[str]:
    return [] if answer.lower() == NONE else [part.strip() for part in answer.split(",") if part.strip()]


def _pick(answer: str, options: Sequence[str]) -> int | None:
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        return int(answer) - 1
    lowered = [option.lower() for option in options]
    return lowered.index(answer.lower()) if answer.lower() in lowered else None


def _index(answer: str, options: Sequence[str]) -> int:
    index = _pick(answer, options)
    if index is None:  # pragma: no cover - answers are validated before they get here
        msg = f"{answer!r} is not one of the options"
        raise ValueError(msg)
    return index


def _unknown(answer: str, options: Sequence[str]) -> str:
    return f"{answer!r} is not one of the options; enter a number from 1 to {len(options)}."
