"""Properties of the contract parser over generated input: contract files are untrusted data (ADR 0021).

`read_contract` turns any exception into the diagnostic "can't read the contract", an isolation boundary. These
properties assert that boundary is never needed: whatever the text, the parser itself reports what is wrong.
fuzz/contract_fuzz.py drives the same parser with atheris for longer, in CI's weekly fuzz run.
"""

import hashlib
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from heinzel.modules.workflow._contracts import CONTROLS, MAX_DEPTH, read_contract

pytestmark = pytest.mark.unit

PATH = Path("digest.prose.md")
BOUNDARY = "can't read the contract"
#: More examples than the profile in tests/unit/conftest.py gives: the parser is fast and the input is untrusted.
QUICK = settings(max_examples=60)

#: Text that looks like a contract often enough to reach the YAML and body checks, not only the frontmatter split.
YAMLISH = st.text(alphabet=st.sampled_from(list("abz019 :-_[]{},'\"#&*!|>?\n\t")), max_size=200)
FRONTMATTER = st.builds(lambda yaml, body: f"---\n{yaml}\n---\n{body}", YAMLISH, st.text(max_size=200))


def reported_by_the_parser(text: str) -> list[str]:
    """The diagnostics' messages, after asserting none of them comes from the isolation boundary."""
    contract = read_contract(PATH, "me", text)
    messages = [d.message for d in contract.diagnostics]
    assert not [m for m in messages if m.startswith(BOUNDARY)], messages
    return messages


@QUICK
@given(st.text(max_size=400) | FRONTMATTER)
def test_any_text_gets_diagnostics_from_the_parser_never_from_an_exception(text):
    contract = read_contract(PATH, "me", text)
    reported_by_the_parser(text)
    assert all(d.line >= 1 for d in contract.diagnostics)


@QUICK
@given(FRONTMATTER)
def test_a_contract_reads_the_same_every_time(text):
    assert read_contract(PATH, "me", text) == read_contract(PATH, "me", text)


@QUICK
@given(st.text(max_size=200))
def test_the_digest_pins_the_exact_bytes(text):
    contract = read_contract(PATH, "me", text)
    assert contract.sha256 in {"", "sha256:" + hashlib.sha256(text.encode()).hexdigest()}


@QUICK
@given(FRONTMATTER, st.sampled_from(["\x1b[31m", "\x1b]52;c;aGk=\x07", "\x9b", "\x00", "\x7f"]))
def test_control_characters_refuse_the_whole_file(text, control):
    contract = read_contract(PATH, "me", text + control)
    assert contract.frontmatter is None
    assert contract.body == ""
    assert any(d.code == "HZC005" for d in contract.diagnostics)
    assert CONTROLS.search(control)


@QUICK
@given(st.integers(min_value=1, max_value=MAX_DEPTH + 8))
def test_nesting_is_refused_exactly_beyond_the_limit(depth):
    yaml = "".join(f"{'  ' * level}k{level}:\n" for level in range(depth)) + f"{'  ' * depth}leaf"
    messages = reported_by_the_parser(f"---\n{yaml}\n---\n")
    assert any(m.startswith("nested deeper") for m in messages) == (depth > MAX_DEPTH)
