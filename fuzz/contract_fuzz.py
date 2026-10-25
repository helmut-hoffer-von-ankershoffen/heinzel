"""Fuzz the workflow contract parser with atheris: whatever the bytes, the parser reports, it never raises.

Contract files are untrusted data (ADR 0021). `read_contract` turns any exception into the diagnostic "can't read
the contract", an isolation boundary; this target fails when that boundary is needed, or when parsing hangs.
Run it with `make fuzz` (Linux, Python 3.12 to 3.14, where atheris has wheels); CI runs it weekly (fuzz.yml).
The hypothesis properties in tests/unit/modules/heinzel_contracts_property_test.py check the same in every test run.
"""

import sys
from pathlib import Path

import atheris

with atheris.instrument_imports():
    from heinzel.modules.heinzel._contracts import read_contract

PATH = Path("fuzz.prose.md")
BOUNDARY = "can't read the contract"


def test_one_input(data: bytes) -> None:
    """Parse one input as a contract, bare and inside frontmatter delimiters.

    Raises:
        AssertionError: If the parser raised and the isolation boundary turned that into a diagnostic.
    """
    text = atheris.FuzzedDataProvider(data).ConsumeUnicodeNoSurrogates(len(data))
    for frame in (text, f"---\n{text}\n---\n### Goal\n"):
        contract = read_contract(PATH, "fuzz", frame)
        failed = [d.message for d in contract.diagnostics if d.message.startswith(BOUNDARY)]
        if failed:
            raise AssertionError(failed)


def main() -> None:
    """Run the fuzzer with libFuzzer's command line (`-max_total_time=60`, a corpus directory)."""
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
