"""Bearer tokens from a helper command, refreshed before `exp` and once on a 401 (ADR 0159)."""

import asyncio
import base64
import json
import subprocess

import httpx2
import pytest

from heinzel.token_auth import CommandTokenAuth, TokenCommandError

pytestmark = pytest.mark.unit

NOW = 1_800_000_000.0


def jwt(expires: float | None) -> str:
    def part(data: dict[str, object]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    claims: dict[str, object] = {"sub": "someone"} if expires is None else {"exp": expires}
    return f"{part({'alg': 'RS256'})}.{part(claims)}.c2ln"


class Helper:
    """A helper command: prints the next token, or nothing with a hint, or fails."""

    def __init__(self, *outputs: tuple[int, str, str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, object]] = []

    def __call__(self, command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        self.calls.append({"command": command, **options})
        code, out, err = self.outputs.pop(0)
        return subprocess.CompletedProcess(command, code, out, err)


def auth(helper: Helper, clock: list[float], **options: object) -> CommandTokenAuth:
    return CommandTokenAuth(["sso", "token"], run=helper, clock=lambda: clock[0], **options)  # type: ignore[arg-type]


def test_a_token_is_kept_until_shortly_before_it_expires():
    first, second = jwt(NOW + 18 * 3600), jwt(NOW + 36 * 3600)
    helper, clock = Helper((0, first + "\n", ""), (0, second, "")), [NOW]
    tokens = auth(helper, clock)
    assert tokens.token() == first
    clock[0] = NOW + 18 * 3600 - 301
    assert tokens.token() == first
    clock[0] = NOW + 18 * 3600 - 299  # inside the five-minute margin
    assert tokens.token() == second
    assert len(helper.calls) == 2
    call = helper.calls[0]
    assert (call["command"], call["shell"], call["timeout"], call["check"]) == (["sso", "token"], False, 30.0, False)


def test_a_token_without_exp_is_kept_until_a_401():
    helper, clock = Helper((0, "opaque-token", ""), (0, "next-token", "")), [NOW]
    tokens = auth(helper, clock)
    assert tokens.token() == "opaque-token"
    clock[0] += 10**6
    assert tokens.token() == "opaque-token"
    assert tokens.token(renew=True) == "next-token"


def test_an_expired_sign_in_is_a_clear_error_with_the_helpers_hint_and_no_request():
    helper = Helper((0, "", "session expired: run `sso login`\n"))
    sent: list[httpx2.Request] = []
    client = httpx2.Client(auth=auth(helper, [NOW]), transport=httpx2.MockTransport(sent.append))  # type: ignore[arg-type]
    with pytest.raises(
        TokenCommandError, match=r"printed no token; sign in with it again: session expired: run `sso login`"
    ):
        client.get("https://ai.example.invalid/v1/messages")
    assert sent == []


@pytest.mark.parametrize(
    ("output", "message"),
    [
        ((3, "secret-partial", "denied"), r"failed \(exit 3\): denied"),
        ((0, "two words", ""), "printed more than one token"),
        ((0, "x" * 20000, ""), "printed more than one token"),
    ],
)
def test_a_failing_or_garbled_helper_never_shows_its_output(output, message):
    with pytest.raises(TokenCommandError, match=message) as failed:
        auth(Helper(output), [NOW]).token()
    assert output[1] not in str(failed.value)


def test_a_helper_that_hangs_or_is_missing_is_a_clear_error():
    def hang(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, 1.0)

    with pytest.raises(TokenCommandError, match="took longer than 30s"):
        CommandTokenAuth(["sso"], run=hang).token()

    def missing(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(2, "No such file or directory")

    with pytest.raises(TokenCommandError, match="couldn't run: No such file or directory"):
        CommandTokenAuth(["sso"], run=missing).token()


def test_a_401_fetches_a_new_token_once_and_drops_named_headers():
    first, second = jwt(NOW + 3600), jwt(NOW + 7200)
    helper = Helper((0, first, ""), (0, second, ""))
    seen: list[dict[str, str]] = []

    def server(request: httpx2.Request) -> httpx2.Response:
        seen.append(dict(request.headers))
        return httpx2.Response(401)

    client = httpx2.Client(
        auth=auth(helper, [NOW], drop=["x-api-key"]), transport=httpx2.MockTransport(server), headers={"x-api-key": "k"}
    )
    assert client.get("https://ai.example.invalid/").status_code == 401, "a second 401 is the endpoint's answer"
    assert [r["authorization"] for r in seen] == [f"Bearer {first}", f"Bearer {second}"]
    assert all("x-api-key" not in r for r in seen)


def test_the_async_flow_runs_the_helper_off_the_loop():
    helper = Helper((0, jwt(NOW + 3600), ""))
    seen: list[str] = []

    def server(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.headers["x-token"])
        return httpx2.Response(200)

    async def call() -> int:
        tokens = auth(helper, [NOW], header="x-token", scheme="")
        async with httpx2.AsyncClient(auth=tokens, transport=httpx2.MockTransport(server)) as client:
            return (await client.get("https://ai.example.invalid/")).status_code

    assert asyncio.run(call()) == 200
    assert seen == [jwt(NOW + 3600)]


def test_the_token_never_shows_in_repr_and_the_command_must_be_set():
    tokens = auth(Helper((0, "tok-123", "")), [NOW])
    tokens.token()
    assert "tok-123" not in repr(tokens)
    with pytest.raises(ValueError, match="non-empty list"):
        CommandTokenAuth([])
    with pytest.raises(ValueError, match="non-empty list"):
        CommandTokenAuth(["sso", ""])
    with pytest.raises(ValueError, match="timeout must be positive"):
        CommandTokenAuth(["sso"], timeout=0)
