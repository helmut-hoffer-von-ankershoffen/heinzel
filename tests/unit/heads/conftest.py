"""Short physical scratch paths for Unix socket tests."""

from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir

import pytest


@pytest.fixture
def short_socket_root() -> Iterator[Path]:
    """Allocate directly under the temporary base, without pytest or xdist path components."""
    base = Path(gettempdir()).resolve()
    if base == Path("/private/tmp"):
        base = Path(__file__).resolve().parents[3] / ".pytest_cache"
        base.mkdir(exist_ok=True)
    with TemporaryDirectory(dir=base, prefix="hz-ipc-") as root:
        yield Path(root)
