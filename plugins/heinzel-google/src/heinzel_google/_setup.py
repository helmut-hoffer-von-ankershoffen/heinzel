"""`hz google setup`, also part of `hz setup`: find `gws`, sign it in read-only, bind the capabilities (ADR 0032)."""

import os
from pathlib import Path

from heinzel import ConfigEditor, HeinzelError, Prompter
from heinzel.kernel import CapabilityView
from heinzel_google._gws import CLIENT, INSTALL, LOGIN, GoogleSession, Gws
from heinzel_google._models import GoogleSettings

CAPABILITIES = ("documents", "calendar")
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
            prompter.say(f"Install gws, then run hz google setup again: {INSTALL}")
            return
        prompter.say(f"Saved to {editor.write('google.gws_path', path)}.")
        session = GoogleSession(settings.model_copy(update={"gws_path": path}), gws)
        executable = session.executable()
    signed_in, detail = session.signed_in()
    prompter.say(detail)
    if not signed_in and "OAuth client" in detail:
        prompter.say(f"Give gws an OAuth client first, then run hz google setup again: {CLIENT}")
        return
    if not signed_in:
        prompter.say(f"Signing in asks Google for read-only access only: {LOGIN}")
        if prompter.confirm("Sign gws in now? It opens your browser"):
            code = gws.login(executable, LOGIN.split()[1:], LOGIN_TIMEOUT)
            prompter.say("gws is signed in." if code == 0 else f"gws auth login did not finish (exit {code}).")
    for capability in CAPABILITIES:
        bound = view.bound(capability)
        if bound == "google":
            continue
        question = f"Read {capability} from Google" + (f" instead of {bound}?" if bound else "?")
        if prompter.confirm(question, default=bound is None):
            prompter.say(f"Saved to {editor.write(f'capabilities.{capability}', 'google')}.")
