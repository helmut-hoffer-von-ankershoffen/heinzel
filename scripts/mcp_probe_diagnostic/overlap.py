"""Reduce private monotonic timestamps to a bounded contention receipt."""

import re
from pathlib import Path


def overlap_milliseconds(directory: Path) -> int | None:
    """Return elapsed overlap, or None if any timestamp is missing or invalid."""
    values = []
    for name in ("negative.start", "negative.end", "companion.start", "companion.end"):
        try:
            data = (directory / name).read_text()
        except OSError:
            return None
        if re.fullmatch(r"[0-9]{1,20}", data) is None:
            return None
        values.append(int(data))
    negative_start, negative_end, companion_start, companion_end = values
    if negative_start >= negative_end or companion_start >= companion_end:
        return None
    overlap = min(negative_end, companion_end) - max(negative_start, companion_start)
    return max(0, overlap // 1_000_000)
