"""What makes `hz setup` one journey: answers given in advance, and where a run stopped (ADR 0032, amended).

An answers file is TOML with one table per module, keys named after the questions (`[org] role = "engineer"`), so a
coding agent or a team can set someone up without a terminal. `<state_dir>/setup.json` records each finished step,
when, and its keyed answers, so a run continues where the last one stopped and `--print-answers` can write the file
back. Neither ever holds a secret: secret questions are never keyed (ADR 0030).
"""

import json
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from heinzel.kernel import HeinzelError, toml_value

STATE_FILE = "setup.json"
STATE_VERSION = 1


def load_answers(path: Path) -> dict[str, object]:
    """The answers in a TOML file, by full key: `[org] role = "engineer"` is `org.role`.

    Raises:
        HeinzelError: If the file can't be read or isn't TOML, naming the file and the problem.
    """
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        msg = f"Can't read the answers file {path}: {exc.strerror or exc}"
        raise HeinzelError(msg) from exc
    except tomllib.TOMLDecodeError as exc:
        msg = f"The answers file {path} is not valid TOML: {exc}"
        raise HeinzelError(msg) from exc
    return dict(_flatten(data))


def _flatten(table: Mapping[str, Any], prefix: str = "") -> list[tuple[str, object]]:
    pairs: list[tuple[str, object]] = []
    for key, value in table.items():
        full = f"{prefix}{key}"
        if isinstance(value, Mapping):
            pairs += _flatten(cast("Mapping[str, Any]", value), f"{full}.")
        else:
            pairs.append((full, value))
    return pairs


def answers_toml(answers: Mapping[str, object]) -> str:
    """Answers as an answers file, one table per module, in the order given."""
    sections: dict[str, list[str]] = {}
    for key, value in answers.items():
        section, _, name = key.rpartition(".")
        sections.setdefault(section, []).append(f"{name} = {toml_value(value)}")
    header = "# Answers for `hz setup --answers <file>`; never secrets: those come from `hz system credential set`.\n"
    return header + "".join(f"\n[{section}]\n" + "\n".join(lines) + "\n" for section, lines in sections.items())


@dataclass(frozen=True, slots=True)
class Finished:
    """A step that finished: what became of it, when, and its keyed answers."""

    outcome: str
    at: str
    answers: dict[str, object] = field(default_factory=dict[str, object])


@dataclass(slots=True)
class SetupState:
    """Which steps of `hz setup` finished, kept in `<state_dir>/setup.json` after every step."""

    path: Path
    steps: dict[str, Finished] = field(default_factory=dict[str, Finished])

    @classmethod
    def load(cls, state_dir: Path) -> "SetupState":
        """The state in `state_dir`; empty when there is none, or when it is unreadable (setup then starts over)."""
        path = state_dir / STATE_FILE
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            steps = {
                name: Finished(outcome=str(step["outcome"]), at=str(step["at"]), answers=dict(step.get("answers", {})))
                for name, step in data["steps"].items()
            }
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return cls(path)
        return cls(path, steps)

    def record(self, module: str, outcome: str, answers: Mapping[str, object]) -> None:
        """Record a finished step and write the file at once, so Ctrl-C after it keeps it."""
        at = datetime.now(UTC).isoformat(timespec="seconds")
        self.steps[module] = Finished(outcome=outcome, at=at, answers=dict(answers))
        self._save()

    def clear(self, module: str | None = None) -> None:
        """Forget every step, or only `module`'s: `hz setup --restart`, `hz setup <module> --restart`."""
        if module is not None:
            if self.steps.pop(module, None) is not None:
                self._save()
            return
        self.steps.clear()
        self.path.unlink(missing_ok=True)

    def answers(self) -> dict[str, object]:
        """Every recorded answer, by full key, in the order the steps finished."""
        return {key: value for step in self.steps.values() for key, value in step.answers.items()}

    def _save(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        data = {
            "version": STATE_VERSION,
            "steps": {
                name: {"outcome": step.outcome, "at": step.at, "answers": step.answers}
                for name, step in self.steps.items()
            },
        }
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        # 0600: the answers name who you are; written whole, so an interrupted run never leaves half a file. A
        # temporary file left over (or planted, or a link) is removed first, so it can't lend its mode or target.
        temporary.unlink(missing_ok=True)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(json.dumps(data, indent=2, default=str) + "\n")
        temporary.replace(self.path)
