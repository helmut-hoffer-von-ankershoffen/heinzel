"""GET a JSON document from PyPI's JSON API: plugin releases and hz's own release metadata (ADR 0116)."""

import json
import urllib.request
from typing import Any

from heinzel import __version__

PYPI = "https://pypi.org"
MAX_BYTES = 4 * 1024 * 1024


def fetch(url: str, timeout: float) -> Any:  # noqa: ANN401 - free-form JSON
    """GET `url` on PyPI with only a `heinzel/<version>` User-Agent; the seam tests fake.

    Raises:
        ValueError: If the URL is not on PyPI or the answer is too large or not JSON.
    """
    if not url.startswith(f"{PYPI}/pypi/"):
        msg = f"not a PyPI JSON URL: {url}"
        raise ValueError(msg)
    request = urllib.request.Request(url, headers={"User-Agent": f"heinzel/{__version__}"})  # noqa: S310 - https
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - https checked above
        body: bytes = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        msg = f"the answer is larger than {MAX_BYTES} bytes"
        raise ValueError(msg)
    return json.loads(body)
