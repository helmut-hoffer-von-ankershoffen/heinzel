"""Questions to a person, in every head that has one: what setup steps ask through (ADR 0032).

Setup steps ask for `Prompter` by type. The questions, defaults, validation and re-asking live here, so every head
behaves the same; a head implements only `read` (one line of input) and `say` (one line of output). The kernel's
default has nobody to ask and refuses; the CLI binds a console prompter, tests a `ScriptedPrompter`.
"""

from collections.abc import Callable, Iterable, Sequence

from heinzel.kernel._errors import HeinzelError

Validate = Callable[[str], str | None]
"""Checks an answer: the problem to show before asking again, or None when it is fine."""

YES = frozenset({"y", "yes"})
NONE = "none"
"""The answer that picks none of several options."""
NO = frozenset({"n", "no"})


class Prompter:
    """Asks a person questions. This base implementation has nobody to ask: every question raises."""

    interactive = False
    """Whether someone answers; heads without a person (MCP, REST, the daemon) keep the default."""

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

    def text(self, question: str, *, default: str | None = None, validate: Validate | None = None) -> str:
        """Free text; an empty answer takes `default`, and is asked again when there is none.

        Raises:
            HeinzelError: If nobody can answer.
        """
        while True:
            answer = self.read(_prompt(question, default)).strip()
            if not answer:
                if default is not None:
                    return default
                self.say("Please answer; there is no default.")
                continue
            if validate is not None and (problem := validate(answer)):
                self.say(problem)
                continue
            return answer

    def secret(self, question: str) -> str:
        """A value that isn't echoed, such as a token; never empty.

        Raises:
            HeinzelError: If nobody can answer.
        """
        while not (answer := self.read(_prompt(question, None), secret=True)).strip():
            self.say("The value is empty; please enter it.")
        return answer.rstrip("\r\n")  # a token is taken as typed; only the line ending goes

    def confirm(self, question: str, *, default: bool = True) -> bool:
        """Yes or no; an empty answer takes `default`.

        Raises:
            HeinzelError: If nobody can answer.
        """
        suffix = "[Y/n]" if default else "[y/N]"
        while True:
            answer = self.read(f"{question} {suffix} ").strip().lower()
            if not answer:
                return default
            if answer in YES | NO:
                return answer in YES
            self.say("Please answer y or n.")

    def number(
        self, question: str, *, default: int | None = None, minimum: int | None = None, maximum: int | None = None
    ) -> int:
        """A whole number between `minimum` and `maximum` (inclusive); an empty answer takes `default`.

        Raises:
            HeinzelError: If nobody can answer.
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

        return int(self.text(question, default=None if default is None else str(default), validate=check))

    def choice(self, question: str, options: Sequence[str], *, default: str | None = None) -> str:
        """One of `options`, by its number or its text (any case); an empty answer takes `default`.

        Raises:
            ValueError: If there are no options, or `default` isn't one of them.
            HeinzelError: If nobody can answer.
        """
        _check_options(options, [] if default is None else [default])
        self._list(options)
        answer = self.text(
            question,
            default=None if default is None else str(options.index(default) + 1),
            validate=lambda a: None if _pick(a, options) is not None else _unknown(a, options),
        )
        return options[_index(answer, options)]

    def choices(self, question: str, options: Sequence[str], *, default: Iterable[str] = ()) -> list[str]:
        """Some of `options`, by numbers or texts separated by commas, or `none`; an empty answer takes `default`.

        Raises:
            ValueError: If there are no options, or a default isn't one of them.
            HeinzelError: If nobody can answer.
        """
        defaults = list(default)
        _check_options(options, defaults)
        self._list(options)

        def check(answer: str) -> str | None:
            unknown = [part for part in _parts(answer) if _pick(part, options) is None]
            return _unknown(unknown[0], options) if unknown else None

        answer = self.text(
            f"{question} (numbers separated by commas, or {NONE})",
            default=",".join(str(options.index(d) + 1) for d in defaults) if defaults else None,
            validate=check,
        )
        if answer.lower() == NONE:
            return []
        picked = [options[_index(part, options)] for part in _parts(answer)]
        return list(dict.fromkeys(picked))

    def _list(self, options: Sequence[str]) -> None:
        for number, option in enumerate(options, start=1):
            self.say(f"  {number}) {option}")


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
