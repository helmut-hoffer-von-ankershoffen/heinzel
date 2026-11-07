"""Sign-ins hz runs for the person through a connector's own provider, without a terminal (ADR 0136).

The provider drives the vendor's tool; this service only decides which grants to ask for, runs one sign-in per
connector in a thread of the serving process, keeps its progress for the web app to poll, stops it on cancel or at
`TIMEOUT`, and opens the vendor's consent page on this machine when asked. It never sees, keeps or logs a secret: a
provider reports a consent page and plain words, nothing else, and nothing here writes a log.
"""

import secrets
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from heinzel import HeinzelError, Kernel, Prompter
from heinzel.kernel import (
    FLOW_ENDED,
    GrantStatus,
    SignInLet,
    SignInOutcome,
    SignInProgress,
    SignInProvider,
    SignInStatus,
)
from heinzel.modules.system.connector._models import Access, SignInFlow

TIMEOUT = 900.0
"""Seconds a sign-in may wait for the person's consent before its tool is stopped."""
KEPT = 20
"""Ended sign-ins kept for polling; older ones are forgotten."""
_STOP_GRACE = 10.0
"""Seconds a waiting caller gives a stopped provider to return."""


def _now() -> datetime:
    return datetime.now(UTC)


def _open(url: str) -> None:
    """Open `url` in this machine's default browser: macOS `open`, elsewhere `xdg-open`; an argument list."""
    command = ["/usr/bin/open", url] if sys.platform == "darwin" else ["xdg-open", url]
    subprocess.run(command, check=True, capture_output=True, timeout=15)  # noqa: S603 - fixed program, https URL


def wanted(statuses: list[GrantStatus], allow: frozenset[str]) -> frozenset[str]:
    """The complete set to ask for: what holds or is needed now, defaults nothing confirms either way, and `allow`.

    A vendor that replaces its scopes on every sign-in must be given all it should keep, so a grant already
    `granted` stays; a `missing` default stays out unless needed, since the person removed it.
    """
    keep = {
        s.grant.id for s in statuses if s.state == "granted" or s.needed or (s.grant.default and s.state == "unknown")
    }
    return frozenset(keep | allow)


@dataclass(slots=True)
class _Running:
    flow: SignInFlow
    stop: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    timed_out: bool = False
    cancelled: bool = False


class SignIns:
    """Runs and tracks connector sign-ins in this process; the web app polls them, the CLI waits for one."""

    def __init__(self, kernel: Kernel) -> None:
        """Work on the kernel, injected."""
        self._kernel = kernel
        self._lock = threading.Lock()
        self._flows: dict[str, _Running] = {}
        self.clock: Callable[[], datetime] = _now
        self.opener: Callable[[str], None] = _open
        self.timeout = TIMEOUT

    def _let(self, name: str) -> SignInLet:
        lets = {record.qualname: let for record, let in self._kernel.lets(SignInLet)}
        if name not in lets:
            known = ", ".join(sorted(lets)) or "none is installed"
            msg = (
                f"{name!r} has no sign-in hz can run; the connectors that have one are: {known}. "
                "`hz system connector list` shows how to sign the others in."
            )
            raise HeinzelError(msg)
        return lets[name]

    def _provider(self, let: SignInLet) -> SignInProvider:
        return self._kernel.call(let.factory)

    def supported(self) -> frozenset[str]:
        """The connectors with a sign-in hz can run, by qualified name."""
        return frozenset(record.qualname for record, _ in self._kernel.lets(SignInLet))

    def _status(self, let: SignInLet, *, live: bool) -> SignInStatus:
        """The provider's status with every declared grant listed; a failing provider is an unknown status."""
        try:
            status = self._provider(let).status(live=live)
        except HeinzelError as exc:
            status = SignInStatus(signed_in=None, problem=str(exc), live=live)
        except Exception as exc:  # noqa: BLE001 - isolate plugins; their messages may carry what a page shouldn't show
            status = SignInStatus(signed_in=None, problem=f"the sign-in status failed ({type(exc).__name__})")
        known = {s.grant.id for s in status.grants}
        missing = [GrantStatus(grant=g, state="unknown", needed=False) for g in let.grants if g.id not in known]
        declared = {g.id for g in let.grants}
        return status.model_copy(update={"grants": [s for s in status.grants if s.grant.id in declared] + missing})

    def access(self, name: str, title: str, *, live: bool = False) -> Access:
        """`name`'s sign-in status, and its latest sign-in run here.

        Raises:
            HeinzelError: If the connector has no sign-in hz can run.
        """
        status = self._status(self._let(name), live=live)
        return Access(name=name, title=title, status=status, flow=self._latest(name))

    def _latest(self, name: str) -> SignInFlow | None:
        with self._lock:
            mine = [r.flow for r in self._flows.values() if r.flow.connector == name]
        return mine[-1].model_copy(deep=True) if mine else None

    def connect(self, name: str, allow: frozenset[str], *, wait: bool, prompter: Prompter) -> SignInFlow:
        """Start `name`'s sign-in asking for what it holds or needs plus `allow`; wait for it when `wait`.

        A sign-in of the same connector still running is stopped first: the person asked again.

        Raises:
            HeinzelError: If there is no such sign-in, a grant in `allow` is unknown, or its tool can't run.
            KeyboardInterrupt: If the person stops a waiting sign-in; its tool is stopped first.
        """
        let = self._let(name)
        declared = {g.id for g in let.grants}
        if unknown := sorted(allow - declared):
            msg = f"{name} has no grant {', '.join(unknown)}; its grants are: {', '.join(sorted(declared))}."
            raise HeinzelError(msg)
        status = self._status(let, live=True)
        if status.tool != "ready":
            fix = f" Install it: {status.install}" if status.install else ""
            msg = f"{name}'s sign-in can't run: {status.problem or 'its tool is ' + status.tool}.{fix}"
            raise HeinzelError(msg)
        # Nothing holds, is needed or allowed (nothing bound yet): a first sign-in asks for the defaults.
        grants = wanted(status.grants, allow) or frozenset(g.id for g in let.grants if g.default)
        provider = self._provider(let)
        running = _Running(
            SignInFlow(
                id=secrets.token_urlsafe(9),
                connector=name,
                state="starting",
                grants=sorted(grants),
                started_at=self.clock(),
            )
        )
        with self._lock:
            for other in self._flows.values():
                if other.flow.connector == name and other.flow.state not in FLOW_ENDED:
                    other.cancelled = True
                    other.stop.set()
            self._flows[running.flow.id] = running
            self._forget()
        progress = self._reporter(running, prompter if wait else None)
        thread = threading.Thread(target=self._run, args=(running, provider, grants, progress), daemon=True)
        timer = threading.Timer(self.timeout, self._expire, args=(running,))
        timer.daemon = True
        thread.start()
        timer.start()
        if wait:
            try:
                running.done.wait()
            except KeyboardInterrupt:  # Ctrl-C in the terminal: stop the vendor's tool rather than orphan it
                running.cancelled = True
                running.stop.set()
                running.done.wait(_STOP_GRACE)
                raise
            finally:
                timer.cancel()
        return self.flow(running.flow.id)

    def _reporter(self, running: _Running, prompter: Prompter | None) -> Callable[[SignInProgress], None]:
        """What the provider calls with the consent page; a waiting caller is told once and the page opened here."""
        said = threading.Event()

        def progress(update: SignInProgress) -> None:
            with self._lock:
                if running.flow.state in FLOW_ENDED:
                    return
                running.flow = running.flow.model_copy(update={"state": "waiting", "progress": update})
            if prompter is None or said.is_set():
                return
            said.set()
            prompter.say(f"Sign in on this page: {update.url}")
            if update.code:
                prompter.say(f"Enter this code there: {update.code}")
            try:
                self.opener(update.url)
            except (OSError, subprocess.SubprocessError):
                prompter.say("No browser opened here: open the page yourself, on this machine.")

        return progress

    def _run(
        self,
        running: _Running,
        provider: SignInProvider,
        grants: frozenset[str],
        progress: Callable[[SignInProgress], None],
    ) -> None:
        try:
            outcome = provider.sign_in(grants, progress, running.stop)
        except HeinzelError as exc:
            outcome = SignInOutcome(state="failed", message=str(exc))
        except Exception as exc:  # noqa: BLE001 - isolate plugins; their messages may carry what a page shouldn't show
            outcome = SignInOutcome(state="failed", message=f"The sign-in failed ({type(exc).__name__}).")
        state = outcome.state
        message = outcome.message
        if running.timed_out and state != "succeeded":
            state, message = "timed-out", f"No consent arrived within {self.timeout / 60:g} minutes; try again."
        elif running.cancelled and state != "succeeded":
            state, message = "cancelled", "The sign-in was cancelled; nothing changed."
        with self._lock:
            running.flow = running.flow.model_copy(
                update={"state": state, "message": message, "account": outcome.account, "ended_at": self.clock()}
            )
        running.done.set()

    def _expire(self, running: _Running) -> None:
        if not running.done.is_set():
            running.timed_out = True
            running.stop.set()

    def _forget(self) -> None:
        """Drop the oldest ended sign-ins beyond `KEPT`; called with the lock held."""
        ended = [key for key, r in self._flows.items() if r.flow.state in FLOW_ENDED]
        for key in ended[: max(0, len(self._flows) - KEPT)]:
            del self._flows[key]

    def _running(self, flow: str) -> _Running:
        with self._lock:
            found = self._flows.get(flow)
        if found is None:
            msg = (
                f"No sign-in {flow!r} is known here; it ended long ago or hz restarted. "
                "Start one: hz system connector connect <name>."
            )
            raise HeinzelError(msg)
        return found

    def flow(self, flow: str) -> SignInFlow:
        """The sign-in `flow` as it stands.

        Raises:
            HeinzelError: If no sign-in of that id is known in this process.
        """
        running = self._running(flow)
        with self._lock:
            return running.flow.model_copy(deep=True)

    def cancel(self, flow: str) -> SignInFlow:
        """Stop the sign-in `flow` and its tool; nothing changes in the vendor's account.

        Raises:
            HeinzelError: If no sign-in of that id is known in this process.
        """
        running = self._running(flow)
        if not running.done.is_set():
            running.cancelled = True
            running.stop.set()
            running.done.wait(_STOP_GRACE)
        return self.flow(flow)

    def open(self, flow: str) -> SignInFlow:
        """Open the sign-in's consent page in the default browser of the machine hz runs on.

        Raises:
            HeinzelError: If the sign-in is unknown, has no consent page (yet), ended, or no browser opens.
        """
        current = self.flow(flow)
        if current.state in FLOW_ENDED:
            msg = f"The sign-in already ended ({current.state}); start a new one."
            raise HeinzelError(msg)
        if current.progress is None:
            msg = "The sign-in has no consent page yet; try again in a moment."
            raise HeinzelError(msg)
        try:
            self.opener(current.progress.url)
        except (OSError, subprocess.SubprocessError) as exc:
            msg = f"No browser opened on this machine ({type(exc).__name__}); open the page from here instead."
            raise HeinzelError(msg) from None
        return current

    def disconnect(self, name: str, title: str) -> Access:
        """Sign `name`'s tool out through its own command, then say how it stands.

        Raises:
            HeinzelError: If there is no such sign-in, or its tool can't sign out.
        """
        let = self._let(name)
        message = self._provider(let).sign_out()
        access = self.access(name, title)
        return access.model_copy(update={"message": message})
