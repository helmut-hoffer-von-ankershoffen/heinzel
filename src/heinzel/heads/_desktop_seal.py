"""Keep the verified, unsigned desktop bundle openable: a valid ad-hoc seal and honest quarantine handling.

macOS on Apple silicon refuses to run arm64 code without a valid signature. A quarantined bundle whose
signature is missing or broken is reported as "damaged" with only a Move to Trash choice. A bundle with a
valid ad-hoc signature is merely "not notarized": it opens directly when it is not quarantined, and otherwise
macOS offers its standard Open Anyway path in System Settings.

Heinzel downloads the release itself (no browser, so macOS adds no quarantine), checks its sha256 against the
release manifest and its build provenance. Only after that does the installer check the seal, add a local
ad-hoc seal when a release shipped without one, and leave the bundle unquarantined unless asked to keep it.
Nothing here ever launches the app.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from heinzel import HeinzelError

_CODESIGN = "/usr/bin/codesign"
_XATTR = "/usr/bin/xattr"
_SPCTL = "/usr/sbin/spctl"
QUARANTINE = "com.apple.quarantine"
AGENT = "Heinzel"
"""The quarantine agent name the installer writes, so only our own mark is ever removed."""
_OUR_MARK = f"0081;00000000;{AGENT};"
_TIMEOUT = 120

type QuarantineState = Literal["none", "ours", "other"]


class DesktopSealError(HeinzelError):
    """The desktop bundle's code signature could not be made valid."""


def _run(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed system executables and validated bundle paths
        command, check=False, capture_output=True, text=True, timeout=_TIMEOUT
    )


def _last_line(result: subprocess.CompletedProcess[str]) -> str:
    lines = [line.strip() for line in (result.stderr + result.stdout).splitlines() if line.strip()]
    return lines[-1] if lines else f"exit status {result.returncode}"


def seal_problem(bundle: Path) -> str | None:
    """Return why the bundle's signature is not valid, or None when `codesign --verify --deep --strict` passes."""
    result = _run(_CODESIGN, "--verify", "--deep", "--strict", str(bundle))
    return None if result.returncode == 0 else _last_line(result)


def ensure_sealed(bundle: Path) -> bool:
    """Make the bundle's code seal valid, ad-hoc signing it locally only when it is broken.

    Returns:
        True when the bundle had to be sealed locally, False when it already carried a valid seal.

    Raises:
        DesktopSealError: If the bundle still has no valid signature afterwards.
    """
    if seal_problem(bundle) is None:
        return False
    signed = _run(_CODESIGN, "--force", "--deep", "--sign", "-", str(bundle))
    problem = _last_line(signed) if signed.returncode else seal_problem(bundle)
    if problem is not None:
        msg = f"macOS would report the desktop app as damaged: its code signature is not valid ({problem})."
        raise DesktopSealError(msg)
    return True


def quarantine_state(bundle: Path) -> QuarantineState:
    """Say whether the bundle carries no quarantine, the installer's own mark or someone else's."""
    result = _run(_XATTR, "-p", QUARANTINE, str(bundle))
    if result.returncode:
        return "none"
    fields = result.stdout.strip().split(";")
    return "ours" if len(fields) > 2 and fields[2] == AGENT else "other"  # noqa: PLR2004 - flags;time;agent


def mark_quarantined(bundle: Path) -> None:
    """Mark the bundle as downloaded so macOS asks once before the first open.

    Raises:
        DesktopSealError: If the attribute cannot be written.
    """
    result = _run(_XATTR, "-w", QUARANTINE, _OUR_MARK, str(bundle))
    if result.returncode:
        msg = f"The desktop app could not be marked as downloaded ({_last_line(result)})."
        raise DesktopSealError(msg)


def clear_our_quarantine(bundle: Path) -> bool:
    """Remove the installer's own quarantine mark from the bundle root; never another program's.

    Returns:
        True when our mark was removed.

    Raises:
        DesktopSealError: If our mark is present but cannot be removed.
    """
    if quarantine_state(bundle) != "ours":
        return False
    result = _run(_XATTR, "-d", QUARANTINE, str(bundle))
    if result.returncode:
        msg = f"The desktop app's quarantine mark could not be removed ({_last_line(result)})."
        raise DesktopSealError(msg)
    return True


def gatekeeper_verdict(bundle: Path) -> str:
    """Return the `spctl --assess` verdict line, which never launches the app."""
    return _last_line(_run(_SPCTL, "--assess", "-vv", "--type", "exec", str(bundle)))


@dataclass(frozen=True)
class SealReport:
    """What macOS will make of the installed bundle, in plain words."""

    bundle: Path
    problem: str | None
    quarantine: QuarantineState
    gatekeeper: str

    @property
    def opens(self) -> bool:
        """Whether a double-click opens the app without the 'damaged' refusal."""
        return self.problem is None

    def lines(self) -> list[str]:
        """Describe the state for a person, one fact per line."""
        signature = (
            "Signature: valid ad-hoc seal (unsigned app from this project, not notarized by Apple)."
            if self.problem is None
            else f"Signature: broken ({self.problem}). macOS will call the app damaged."
        )
        quarantine = {
            "none": "Quarantine: none. A double-click opens the app.",
            "ours": "Quarantine: set by Heinzel. macOS asks once; open it via System Settings, Privacy & Security, "
            "Open Anyway.",
            "other": "Quarantine: set by another program. macOS asks once; open it via System Settings, "
            "Privacy & Security, Open Anyway.",
        }[self.quarantine]
        return [f"App: {self.bundle}", signature, quarantine, f"Gatekeeper assessment: {self.gatekeeper}"]


def inspect_bundle(bundle: Path) -> SealReport:
    """Check signature, quarantine and Gatekeeper's verdict without launching anything."""
    return SealReport(bundle, seal_problem(bundle), quarantine_state(bundle), gatekeeper_verdict(bundle))
