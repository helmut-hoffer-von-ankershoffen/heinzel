"""An attested archive still has to obey the local extraction boundary."""

import io
import tarfile

import pytest

from heinzel import HeinzelError
from heinzel.heads._desktop_archive import BUNDLE_NAME, extract_bundle

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("name", "link"),
    [
        ("../escape", None),
        (f"{BUNDLE_NAME}/escape", "../../outside"),
        (f"{BUNDLE_NAME}/indirect", "alias/../outside"),
        (BUNDLE_NAME.lower(), None),
        (f"{BUNDLE_NAME}/missing-parent/file", None),
    ],
)
def test_rejects_entire_archive_before_any_extraction(tmp_path, name, link):
    archive = tmp_path / "desktop.tar.gz"
    staging = tmp_path / "staging"
    staging.mkdir()
    with tarfile.open(archive, "w:gz") as output:
        root = tarfile.TarInfo(BUNDLE_NAME)
        root.type = tarfile.DIRTYPE
        output.addfile(root)
        candidate = tarfile.TarInfo(name)
        if link is None:
            candidate.size = 1
            output.addfile(candidate, io.BytesIO(b"x"))
        else:
            candidate.type = tarfile.SYMTYPE
            candidate.linkname = link
            output.addfile(candidate)
    with pytest.raises(HeinzelError, match="unsafe"):
        extract_bundle(archive, staging)
    assert not list(staging.iterdir())
