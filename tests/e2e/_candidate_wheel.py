"""Bind real lifecycle candidate wheels to copied source metadata, without relabeling releases."""

import email.parser
import tomllib
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CANDIDATE_VERSION = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]


def candidate_wheel(root: Path) -> Path:
    """Require the source, locked project, filename and wheel metadata to name the exact current candidate."""
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    assert project["name"] == "heinzel"
    assert project["version"] == CANDIDATE_VERSION
    locked = [
        package for package in tomllib.loads((root / "uv.lock").read_text())["package"] if package["name"] == "heinzel"
    ]
    assert len(locked) == 1
    assert locked[0]["version"] == CANDIDATE_VERSION
    target = root / f"dist/heinzel-{CANDIDATE_VERSION}-py3-none-any.whl"
    assert target.is_file()
    with zipfile.ZipFile(target) as archive:
        names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        assert names == [f"heinzel-{CANDIDATE_VERSION}.dist-info/METADATA"]
        metadata = email.parser.BytesParser().parsebytes(archive.read(names[0]))
        assert metadata.get_all("Name") == ["heinzel"]
        assert metadata.get_all("Version") == [CANDIDATE_VERSION]
    return target
