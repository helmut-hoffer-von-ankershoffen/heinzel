from collections.abc import Callable
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, Field

from heinzel import Config, Module, ModuleSettings
from heinzel.modules.system.config import ConfigService
from heinzel.modules.system.config._schema import setting_schemas

pytestmark = pytest.mark.unit


class Room(BaseModel):
    """A meeting room."""

    name: str = Field(description="What it is called.")
    seats: Annotated[int, Field(ge=1, le=40)] = 4


class Floor(BaseModel):
    """A floor of rooms."""

    rooms: list[Room] = Field(default_factory=list, description="Its rooms.")


class Node(BaseModel):
    """A tree."""

    children: list["Node"] = Field(default_factory=list)


class OfficeSettings(ModuleSettings):
    """Settings of the office module."""

    floors: list[Floor] = Field(default=[], description="The floors.")
    lobby: Room = Field(default=Room(name="Lobby"), description="Where guests wait.")
    mood: Literal["calm", "busy"] = Field(default="calm", description="How it feels.")
    tree: Node = Field(default=Node(), description="A recursive model.")


def test_nested_models_are_resolved_inline_with_their_bounds_and_descriptions():
    floors = setting_schemas(OfficeSettings)["floors"]
    room = floors["items"]["properties"]["rooms"]["items"]
    assert floors["description"] == "The floors."
    assert room["properties"]["seats"] == {
        "default": 4,
        "maximum": 40,
        "minimum": 1,
        "title": "Seats",
        "type": "integer",
    }
    assert room["properties"]["name"]["description"] == "What it is called."
    assert "$ref" not in str(floors)


def test_a_referenced_model_keeps_the_fields_own_description_and_default():
    lobby = setting_schemas(OfficeSettings)["lobby"]
    assert (lobby["description"], lobby["default"]) == ("Where guests wait.", {"name": "Lobby", "seats": 4})
    assert lobby["properties"]["name"]["type"] == "string"


def test_enums_are_kept():
    assert setting_schemas(OfficeSettings)["mood"]["enum"] == ["calm", "busy"]


def test_a_recursive_model_is_described_once_without_a_ref():
    tree = setting_schemas(OfficeSettings)["tree"]
    assert tree["properties"]["children"]["items"] == {}
    assert "$ref" not in str(tree)


class OpaqueSettings(ModuleSettings):
    """A setting JSON Schema can't describe."""

    check: Callable[[], int] = Field(default=int, description="A callable.")


class UnserializableDefaultSettings(ModuleSettings):
    """A setting whose default JSON can't hold."""

    kind: type[int] = Field(default=int, description="A class.")


def test_a_default_json_cannot_hold_is_left_out_of_the_schema():
    assert "default" not in setting_schemas(UnserializableDefaultSettings)["kind"]


def test_a_model_without_a_json_schema_gives_no_schemas():
    assert setting_schemas(OpaqueSettings) == {}


def test_every_setting_reports_its_schema(make_kernel):
    module = Module("office", "x")
    module.settings(OfficeSettings)
    kernel = make_kernel(office=module, config=Config(files=(), data={}))
    assert ConfigService(kernel).get("office.mood").json_schema == {
        "default": "calm",
        "description": "How it feels.",
        "enum": ["calm", "busy"],
        "title": "Mood",
        "type": "string",
    }
    assert ConfigService(kernel).get("hz.log_level").json_schema is not None


def test_settings_whose_values_depend_on_what_is_installed_name_their_source():
    """ADR 0056: the web app offers these as a choice; without `x-options` they would be free text."""
    from heinzel import Settings
    from heinzel.kernel import CAPABILITIES
    from heinzel.kernel._bindings import CapabilitySettings
    from heinzel.modules.system.theme._models import ThemeSettings
    from heinzel.modules.team._models import TeamSettings

    bindings = setting_schemas(CapabilitySettings)
    assert {name: schema["x-options"]["capability"] for name, schema in bindings.items()} == {
        name.replace("-", "_"): name for name in CAPABILITIES
    }
    assert setting_schemas(ThemeSettings)["name"]["x-options"] == {"source": "themes"}
    assert setting_schemas(TeamSettings)["layer"]["x-options"] == {"source": "layers"}
    assert setting_schemas(Settings)["disabled_modules"]["x-options"] == {"source": "modules"}
