"""Sonar's overall-code ratings are judged inside CI, and anything below A, or no answer, fails."""

import pytest

pytestmark = pytest.mark.unit

PROJECT = "owner_thing"


@pytest.fixture
def sonar(monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from scripts import sonar_ratings

    return sonar_ratings


def _answer(**values: str) -> dict:
    measures = [{"metric": metric, "value": value} for metric, value in values.items()]
    return {"component": {"key": PROJECT, "measures": measures}}


ALL_A = {"reliability_rating": "1.0", "security_rating": "1.0", "sqale_rating": "1.0"}


def _run(sonar, tmp_path, read) -> tuple[int, str, list[float]]:
    slept: list[float] = []
    env = {"GITHUB_REPOSITORY": "owner/thing", "GITHUB_OUTPUT": str(tmp_path / "out")}
    code = sonar.main(env, read, slept.append)
    return code, (tmp_path / "out").read_text(encoding="utf-8"), slept


def test_all_three_ratings_a_pass(sonar, tmp_path):
    seen: list[str] = []

    def read(url: str) -> dict:
        seen.append(url)
        return _answer(**ALL_A)

    code, output, _ = _run(sonar, tmp_path, read)
    assert code == 0
    assert output == "verdict=Sonar ratings passed: reliability, security and maintainability are A\n"
    assert seen == [f"{sonar.API}?component={PROJECT}&metricKeys=reliability_rating%2Csecurity_rating%2Csqale_rating"]


@pytest.mark.parametrize(
    "case",
    [
        ({**ALL_A, "security_rating": "2.0"}, "security is B, needs A"),
        ({**ALL_A, "sqale_rating": "5.0"}, "maintainability is E, needs A"),
        ({"reliability_rating": "1.0", "security_rating": "1.0"}, "maintainability is not reported, needs A"),
    ],
)
def test_a_rating_below_a_fails_as_not_met(sonar, tmp_path, case):
    values, reason = case
    code, output, _ = _run(sonar, tmp_path, lambda _url: _answer(**values))
    assert code == 1
    assert output.startswith("verdict=Sonar ratings FAILED (not met): ")
    assert reason in output


@pytest.mark.parametrize(
    "answer",
    [{"errors": [{"msg": "Component key not found"}]}, {"component": {"key": "other"}}, ["not", "a", "dict"]],
)
def test_an_unusable_answer_is_a_tool_error_after_retries(sonar, tmp_path, answer):
    code, output, slept = _run(sonar, tmp_path, lambda _url: answer)
    assert code == 2
    assert output.startswith("verdict=Sonar ratings TOOL ERROR (no answer, the ratings were not judged): ")
    assert slept == [sonar.PAUSE] * (sonar.TRIES - 1)


def test_an_unreachable_api_is_retried_then_a_tool_error(sonar, tmp_path):
    def read(_url: str) -> dict:
        message = "SonarCloud did not answer: timed out"
        raise sonar.ToolError(message)

    code, output, slept = _run(sonar, tmp_path, read)
    assert code == 2
    assert "SonarCloud did not answer: timed out" in output
    assert len(slept) == sonar.TRIES - 1


def test_a_late_answer_still_counts(sonar, tmp_path):
    answers = iter([sonar.ToolError("down"), _answer(**ALL_A)])

    def read(_url: str) -> dict:
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    code, _, slept = _run(sonar, tmp_path, read)
    assert code == 0
    assert slept == [sonar.PAUSE]


def test_no_repository_is_a_tool_error(sonar, tmp_path):
    env = {"GITHUB_OUTPUT": str(tmp_path / "out")}
    assert sonar.main(env, lambda _url: _answer(**ALL_A), lambda _s: None) == 2
