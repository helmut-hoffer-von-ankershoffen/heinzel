# /// script
# requires-python = ">=3.15"
# dependencies = []
# ///
"""Reject missing or skipped Apple CI fixtures from the pytest JUnit report."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

root = ET.parse(Path(sys.argv[1])).getroot()  # noqa: S314 - generated locally by pytest
cases = root.findall(".//testcase")
if not cases or any(case.find("skipped") is not None for case in cases):
    print("Apple CI requires executed fixtures without skips", file=sys.stderr)
    sys.exit(1)
