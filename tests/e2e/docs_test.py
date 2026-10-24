"""The README's install, use and uninstall instructions work, run verbatim on macOS and Linux.

Every fenced block right after a `<!-- docs-test -->` marker is run with bash in a sandbox, in order, except that the
block that uninstalls runs last. Only the GitHub URL of Heinzelmännchen is redirected, to this checkout; first-party
plugins like `clock` then come from the same checkout. Needs network (PyPI) and a git checkout with at least one
commit. `hz system changelog --newer` asks GitHub: CI passes its read-only `GITHUB_TOKEN`; without a token a
rate-limited answer skips the test with the reason instead of failing it.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

REPO = Path(__file__).resolve().parents[2]
HEAD = subprocess.run(
    ["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, check=False, timeout=300
).stdout.strip()
HZ_URL = "git+https://github.com/helmut-hoffer-von-ankershoffen/heinzel"
UNINSTALL = "uv tool uninstall heinzel"
TOKENS = ("GH_TOKEN", "GITHUB_TOKEN", "HZ_SYSTEM_CHANGELOG__GITHUB_TOKEN")
RATE_LIMITED = re.compile(r"HTTP Error (403|429): rate limit exceeded", re.IGNORECASE)
MARKER = re.compile(r"<!-- docs-test -->\s*\n```bash\n(.*?)\n```", re.DOTALL)


def documented_blocks(path: Path) -> list[str]:
    """The shell blocks of a document that are marked for testing, the one that uninstalls last."""
    return sorted(MARKER.findall(path.read_text(encoding="utf-8")), key=lambda block: UNINSTALL in block)


def test_the_readme_has_tested_blocks():
    blocks = documented_blocks(REPO / "README.md")
    assert len(blocks) == 3
    assert HZ_URL in blocks[0]
    assert "hz system plugin add clock" in blocks[1]
    assert UNINSTALL in blocks[-1]


def test_readme_instructions(tmp_path):
    if not HEAD:
        pytest.skip("needs a git checkout with at least one commit")
    env = {
        **os.environ,
        "UV_TOOL_DIR": str(tmp_path / "tools"),
        "UV_TOOL_BIN_DIR": str(tmp_path / "bin"),
        "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "HZ_CONFIG_HOME": str(tmp_path / "config"),
        "HZ_DATA_DIR": str(tmp_path / "data"),
        "HZ_CACHE_DIR": str(tmp_path / "cache"),
        "HZ_STATE_DIR": str(tmp_path / "state"),
        "HZ_LOG_DIR": str(tmp_path / "log"),
        "HZ_SYSTEM_DAEMON__UNIT_DIR": str(tmp_path / "units"),
        "HZ_SYSTEM_AGENT__SKILL_DIRS": f'["{tmp_path / "skills"}"]',
    }
    env.pop("VIRTUAL_ENV", None)

    for block in documented_blocks(REPO / "README.md"):
        script = block.replace(HZ_URL, f"git+file://{REPO}@{HEAD}")
        result = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", script],
            capture_output=True, text=True, check=False, env=env, cwd=tmp_path, timeout=500,
        )  # fmt: skip
        tokenless = not any(os.environ.get(name) for name in TOKENS)
        if result.returncode and tokenless and (limited := RATE_LIMITED.search(result.stderr)):
            pytest.skip(f"GitHub rate-limited the unauthenticated releases API ({limited[0]}); set GH_TOKEN")
        assert result.returncode == 0, f"README block failed:\n{script}\n---\n{result.stdout}\n{result.stderr}"

    assert not (tmp_path / "bin" / "hz").exists()
