"""The owned shell's exact, versioned pre-import recovery command."""

import json
import os
import re
import shlex
from pathlib import Path

from heinzel import HeinzelError
from heinzel.kernel.install._installation_continuity import tool_python
from heinzel.modules.system.lifecycle import _recovery_boot

MODE = "# sealed-recovery-v1 "
_PATH_LIMIT = 4096


def interpreter(prefix: Path) -> tuple[str, str] | None:
    """Bind a known external Python to the launcher, without executing it."""
    try:
        path, _, _ = tool_python(prefix)
        return path, _recovery_boot.file_digest(Path(path))
    except (HeinzelError, OSError, ValueError):
        return None


def recover_config(content: str) -> tuple[str, str] | None:
    """Read the exact bound interpreter spelling and digest for launcher recognition."""
    selected = [line.removeprefix(MODE) for line in content.splitlines() if line.startswith(MODE)]
    if len(selected) != 1:
        return None
    try:
        path, digest = json.loads(selected[0])
        if (
            isinstance(path, str)
            and isinstance(digest, str)
            and Path(path).is_absolute()
            and len(path) <= _PATH_LIMIT
            and "\x00" not in path
            and "\n" not in path
            and re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            return path, digest
    except (ValueError, TypeError):
        pass
    return None


def interpreter_matches(selected: tuple[str, str], prefix: Path) -> bool:
    """Check that the launcher's exact verifier still runs outside the tool."""
    spelling, digest = selected
    path = Path(spelling)
    try:
        return (
            not path.is_relative_to(prefix) and os.access(path, os.X_OK) and _recovery_boot.file_digest(path) == digest
        )
    except (OSError, ValueError):
        return False


def render(platform: str, prefix: Path, state: Path, home: Path, selected: tuple[str, str] | None) -> str:
    """Run the pinned stdlib verifier only when an automatic update may need recovery."""
    if selected is None:
        return ""
    python, checksum = selected
    binary = shlex.quote(python)
    sum_command = "/usr/bin/shasum -a 256" if platform == "darwin" else "/usr/bin/sha256sum"
    bootstrap = Path(_recovery_boot.__file__).read_text(encoding="utf-8")
    recover = shlex.join(
        ["/usr/bin/env", "-i", f"HOME={home}", python, "-I", "-S", "-B", "-c", bootstrap, str(prefix), str(state)]
    )
    snapshot = shlex.quote(str(state / "auto-update/before-update"))
    mutation = shlex.quote(str(state / "daemon/mutation-pending.json"))
    return (
        MODE + json.dumps(selected) + "\n"
        f"if [ -e {snapshot} ] || [ -L {snapshot} ] || [ -e {mutation} ] || [ -L {mutation} ]; then\n"
        f"  [ -f {binary} ] && [ ! -L {binary} ] || exit 75\n"
        f"  sum=$(/usr/bin/env -i {sum_command} < {binary}) || exit 75\n"
        f'  [ "${{sum%% *}}" = {checksum} ] || exit 75\n'
        f"  {recover} || exit 75\n"
        "fi\n"
    )
