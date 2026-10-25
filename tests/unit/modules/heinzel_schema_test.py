"""The committed JSON Schema of contract frontmatter accepts what the models accept (ADR 0021)."""

import json
from pathlib import Path

import jsonschema
import pytest

from heinzel.modules.heinzel._contracts import _load, _split, read_contract

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parents[3]
SCHEMA = json.loads((ROOT / "docs" / "schemas" / "contract-frontmatter-1.json").read_text())
EXAMPLE = ROOT / "tests" / "fixtures" / "workflows" / "weekly-digest.prose.md"


def frontmatter_of(path: Path) -> dict:
    split = _split(path.read_text())
    assert split is not None
    data, _ = _load(split[0])
    return data


def test_the_example_contract_matches_the_committed_schema():
    jsonschema.validate(frontmatter_of(EXAMPLE), SCHEMA)


def test_the_validated_model_with_defaults_filled_matches_the_committed_schema():
    contract = read_contract(EXAMPLE, "me")
    assert contract.frontmatter is not None
    filled = contract.frontmatter.model_dump(mode="json", by_alias=True, exclude_none=True)
    jsonschema.validate(filled, SCHEMA)
    assert filled["hz"]["timeout"] == "10m"


def test_the_schema_states_defaults_as_they_are_written():
    hz = SCHEMA["$defs"]["Hz"]["properties"]
    assert hz["timeout"]["default"] == "10m"
    assert (hz["outputs"]["minProperties"], hz["outputs"]["maxProperties"]) == (1, 1)
    assert hz["catch_up"]["enum"] == ["latest", "skip"]


def test_the_schema_refuses_two_outputs():
    data = frontmatter_of(EXAMPLE)
    data["hz"]["outputs"]["second"] = {"format": "json"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, SCHEMA)
