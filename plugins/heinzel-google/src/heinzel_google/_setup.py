"""`hz setup google`, also part of `hz setup`: find `gws`, sign it in read-only, bind the capabilities (ADR 0032)."""

import os
from pathlib import Path

from heinzel import ConfigEditor, HeinzelError, Prompter
from heinzel.kernel import CapabilityView
from heinzel_google._gws import CLIENT, INSTALL, LOGIN, LOGIN_WITH_MAIL, GoogleSession, Gws
from heinzel_google._models import GoogleSettings

CAPABILITIES = ("documents", "calendar", "mail")
OPT_IN = frozenset({"mail"})
"""Capabilities offered with "no" as the default: Gmail's read scope covers all of a person's mail."""
LOGIN_TIMEOUT = 900.0
"""Seconds `gws auth login` may take, consent in the browser included."""


def _executable(path: str) -> str | None:
    """Why `path` can't be the gws executable, or None if it can."""
    if not Path(path).is_absolute():
        return "Please give an absolute path, such as /opt/homebrew/bin/gws."
    if not (Path(path).is_file() and os.access(path, os.X_OK)):
        return f"{path} is not an executable file."
    return None


def run_setup(  # noqa: PLR0913, PLR0917 - what the step is injected with
    settings: GoogleSettings,
    session: GoogleSession,
    gws: Gws,
    view: CapabilityView,
    prompter: Prompter,
    editor: ConfigEditor,
) -> None:
    """The setup step's work; see `heinzel_google.setup`."""
    try:
        executable = session.executable()
    except HeinzelError as exc:
        prompter.say(str(exc))
        path = prompter.text("Absolute path of gws, empty to install it first", default="", validate=_executable)
        if not path:
            prompter.say(f"Install gws, then run hz setup google again: {INSTALL}")
            return
        prompter.say(f"Saved to {editor.write('google.gws_path', path)}.")
        session = GoogleSession(settings.model_copy(update={"gws_path": path}), gws)
        executable = session.executable()
    signed_in, detail = session.signed_in()
    prompter.say(detail)
    if not signed_in and "OAuth client" in detail:
        prompter.say(f"Give gws an OAuth client first, then run hz setup google again: {CLIENT}")
        return
    newly = _bind(view, prompter, editor)
    # Mail Google serves only automatically was asked about in _bind: kept, it is in newly; declined, it is off.
    mail = "mail" in newly or view.binding("mail") in {("google", "configured"), ("google", "default")}
    if signed_in and not mail:
        return
    if signed_in and "mail" not in newly:
        # Mail was bound before; whether that sign-in included Gmail's scope, gws's status doesn't say.
        prompter.say(
            f"If gws was signed in before mail was read from Google, it lacks Gmail's scope: {LOGIN_WITH_MAIL}"
        )
        if prompter.confirm("Sign gws in again with Gmail's scope? It opens your browser", default=False):
            code = gws.login(executable, LOGIN_WITH_MAIL.split()[1:], LOGIN_TIMEOUT)
            prompter.say("gws is signed in." if code == 0 else f"gws auth login did not finish (exit {code}).")
        return
    login = LOGIN_WITH_MAIL if mail else LOGIN
    why = "read-only access, to your mail too" if mail else "read-only access only"
    prompter.say(f"Signing in asks Google for {why}: {login}")
    if prompter.confirm("Sign gws in now? It opens your browser"):
        code = gws.login(executable, login.split()[1:], LOGIN_TIMEOUT)
        prompter.say("gws is signed in." if code == 0 else f"gws auth login did not finish (exit {code}).")


def _bind(view: CapabilityView, prompter: Prompter, editor: ConfigEditor) -> set[str]:
    """Offer each capability not chosen to be read from Google yet; the ones now bound to it.

    Google serving a capability automatically, as the only installed adapter (ADR 0056), is no choice of the
    person's: they are asked, and declining writes `none`, so mail stays opt-in even when nothing else provides it.
    """
    newly: set[str] = set()
    for capability in CAPABILITIES:
        bound, how = view.binding(capability)
        if bound == "google" and how != "automatic":
            continue
        automatic = bound == "google"
        other = None if automatic else bound
        question = f"Read {capability} from Google" + (f" instead of {other}" if other else "")
        question += " (this grants read access to all of your mail)?" if capability in OPT_IN else "?"
        if prompter.confirm(question, default=other is None and capability not in OPT_IN):
            prompter.say(f"Saved to {editor.write(f'capabilities.{capability}', 'google')}.")
            newly.add(capability)
        elif automatic:
            prompter.say(f"Not read from Google; saved to {editor.write(f'capabilities.{capability}', 'none')}.")
    return newly
