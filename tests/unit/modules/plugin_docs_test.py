"""`system.plugin.docs`: the model of what a plugin brings, read from the kernel, and the pages written from it."""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import pytest
from pydantic import BaseModel, Field

from heinzel import Check, Event, HeinzelError, Kernel, Let, Module, ModuleSettings, Prompter, Status, Theme, Window
from heinzel.kernel import DEFAULT_THEME, ModuleRecord, Origin, env_prefix, exposed
from heinzel.modules.system.plugin.docs import _build, _render, _service
from heinzel.modules.system.plugin.docs._models import Documentation, Scene, SceneStep
from heinzel.modules.system.plugin.docs._service import DocsService, scenes_from

pytestmark = pytest.mark.unit

ORIGIN = Origin(distribution="heinzel-weather", version="1.2.3", builtin=False, url="https://example.com/weather")


@dataclass(frozen=True, kw_only=True, slots=True)
class WidgetLet(Let):
    """A kind of let the renderer doesn't know yet, as a future UI contribution would be."""

    slot: str


class WeatherSettings(ModuleSettings):
    """Settings of the weather plugin."""

    city: str = Field(default="Cologne", description="Whose weather to show.")
    token_hint: str | None = Field(default=None, description="Where the token is explained.")


class Forecast(BaseModel):
    """What the weather will be."""

    city: str = Field(description="The city.")
    summary: str = Field(description="One line.")


def weather() -> tuple[Module, Module]:
    """A plugin module with every kind of let, and a submodule that collapses into one command."""
    module = Module("weather", "The weather for the team's cities.", requires=["calendar:read"])
    module.settings(WeatherSettings)
    module.credential("api-key", purpose="Read the forecast service; get one at its site.")

    @module.operation(audience="everyday")
    def today(
        settings: WeatherSettings,
        city: Annotated[str | None, Field(description="A city instead of the configured one.")] = None,
    ) -> Forecast:
        """Today's weather.

        Longer words about it.

        Raises:
            HeinzelError: If the city is unknown.
        """
        return Forecast(city=city or settings.city, summary="Sunny")

    @module.operation(read_only=False, heads={"cli"})
    def pin(city: Annotated[str, Field(description="The city to pin.")]) -> Forecast:
        """Pin a city."""
        return Forecast(city=city, summary="Pinned")

    @module.provides("calendar", modes={"read"}, hosts=["api.example.com"])
    class Adapter:
        """A calendar from the weather service."""

        def events(self, window: Window) -> list[Event]:
            """No events."""
            return []

    @module.job(schedule="0 7 * * *")
    def refresh() -> None:
        """Refresh the forecast."""

    @module.health
    def health() -> list[Check]:
        """Checks."""
        return [Check(name="service", status=Status.UP)]

    @module.info
    def info() -> dict[str, str]:
        """Info."""
        return {}

    @module.setup
    def setup(prompter: Prompter) -> None:
        """Pick the city."""

    module.theme(Theme.model_validate(DEFAULT_THEME.model_dump() | {"name": "weather", "title": "Weather"}))
    module.playbook("Weather briefings.", defaults={"capabilities.calendar": "weather"}, setup=False)
    module.add(WidgetLet(name="footer-weather", slot="footer"))

    sub = Module("radar", "The radar image.")

    @sub.operation
    def radar() -> str:
        """The radar as text."""
        return "::"

    return module, sub


@pytest.fixture
def kernel(make_kernel) -> Kernel:
    module, sub = weather()
    records = [
        ModuleRecord(qualname="weather", module=module, origin=ORIGIN),
        ModuleRecord(qualname="weather.radar", module=sub, origin=ORIGIN),
    ]
    k = make_kernel(builtins=True)
    return Kernel(k.settings, [*k.modules, *records], config=k.config)


@pytest.fixture
def documentation(kernel: Kernel) -> Documentation:
    return kernel.resolve(DocsService).documentation(plugin="weather")


def test_the_page_names_every_kind_of_let(documentation):
    (page,) = documentation.pages
    assert page.distribution == "heinzel-weather"
    assert page.name == "weather"
    assert page.kinds == ["connector", "theme", "capability", "playbook"]
    assert page.install == "hz system plugin add git+https://example.com/weather --confirm"
    module, sub = page.modules
    assert module.requires == ["calendar:read"]
    assert [op.command for op in module.operations] == ["hz weather today", "hz weather pin"]
    assert [s.key for s in module.settings] == ["city", "token_hint"]
    assert module.settings[0].environment == env_prefix("weather") + "CITY"
    assert module.settings[0].default == '"Cologne"'
    assert module.settings[1].type == "string or null"  # never a `|`: it sits in a Markdown table cell
    assert module.credentials[0].command == "hz system credential set api-key"
    assert module.health_checks
    assert module.info
    assert module.setup is not None
    assert module.setup.command == "hz setup weather"
    assert page.adapters[0].hosts == ["api.example.com"]
    assert page.adapters[0].binding == "hz system config set capabilities.calendar weather"
    assert page.themes[0].command == "hz system theme use weather"
    assert any(c.token == "primary" and c.light and c.dark for c in page.themes[0].colours)
    assert page.playbooks[0].defaults == {"capabilities.calendar": "weather"}
    assert page.jobs[0].schedule == "0 7 * * *"
    assert page.jobs[0].timeout == "15m"
    assert [(let.kind, let.name) for let in page.other_lets] == [("WidgetLet", "footer-weather")]
    assert sub.operations[0].command == "hz weather radar"  # collapsed: the module is its one operation


def test_operations_are_described_as_every_head_sees_them(documentation):
    (page,) = documentation.pages
    today, pin = page.modules[0].operations
    assert today.usage == "hz weather today [--city TEXT]"
    assert [p.name for p in today.parameters] == ["city"]  # settings are injected, never shown
    assert today.tool == "weather_today"
    assert today.endpoint == "GET /weather/today"
    assert today.web_endpoint == "GET /api/weather/today"
    assert today.description == "Longer words about it."  # the Raises section is for callers in code
    assert today.input_schema["properties"]["city"]["description"] == "A city instead of the configured one."
    assert pin.usage == "hz weather pin CITY"
    assert pin.tool is None
    assert pin.endpoint is None
    assert pin.heads == ["cli"]
    assert not pin.read_only


def test_paths_and_parameters_are_the_exposure_rule(kernel):
    """The same rule the heads use, from the kernel: every CLI command the docs name is one the CLI exposes."""
    docs = kernel.resolve(DocsService).documentation(builtin=True, plugin="heinzel")
    documented = {op.command for page in docs.pages for module in page.modules for op in module.operations}
    cli = {
        " ".join(("hz", *_service.cli_words(item.path)))
        for item in exposed(kernel, "cli")
        if item.record.origin.builtin
    }
    assert documented == cli
    assert "hz chat send" in documented  # the chat is mounted at the top level (ADR 0045)


def test_a_cli_scene_is_derived_from_the_operations(documentation):
    (page,) = documentation.pages
    (scene,) = page.scenes
    assert scene.derived
    assert scene.kind == "cli"
    # --help, then read-only operations without required arguments (everyday ones first), the first also as JSON.
    expected = ["hz weather --help", "hz weather today", "hz weather today --json", "hz weather radar"]
    assert scene.commands == expected


def test_declared_scenes_replace_the_derived_one(kernel, tmp_path):
    scenes = tmp_path / "scenes.toml"
    scenes.write_text(
        '[[cli]]\nname = "forecast"\ntitle = "The forecast"\n'
        'commands = ["hz weather today", "hz weather today --json | jq"]\n'
        '[[ui]]\nname = "footer"\ntitle = "The footer"\n'
        '[[ui.steps]]\naction = "goto"\ntarget = "/"\n'
        '[[ui.steps]]\naction = "hover"\nrole = "button"\ntarget = "Weather"\n'
        '[[ui.steps]]\naction = "screenshot"\ncaption = "The forecast in the footer"\n',
        encoding="utf-8",
    )
    docs = kernel.resolve(DocsService).documentation(plugin="weather", scenes=scenes)
    (page,) = docs.pages
    assert [(s.name, s.kind, s.derived) for s in page.scenes] == [
        ("forecast", "cli", False),
        ("footer", "ui", False),
    ]
    assert page.scenes[1].steps[1].role == "button"


@pytest.mark.parametrize(
    ("toml", "problem"),
    [
        ('[[cli]]\nname = "x"\ntitle = "X"\ncommands = ["echo hi"]\n', "type real `hz` commands"),
        ('[[cli]]\nname = "x"\ntitle = "X"\ncommands = ["hz x; rm -rf /"]\n', "nothing else"),
        ('[[cli]]\nname = "x"\ntitle = "X"\ncommands = ["hz x && curl example.com"]\n', "nothing else"),
        ('[[cli]]\nname = "x"\ntitle = "X"\ncommands = ["hz x | sh"]\n', "nothing else"),
        ('[[cli]]\nname = "x"\ntitle = "X"\ncommands = ["hz x $(date)"]\n', "nothing else"),
        ('[[ui]]\nname = "x"\ntitle = "X"\n[[ui.steps]]\naction = "screenshot"\noptionl = true\n', "is malformed"),
        ('[[cli]]\nname = "Bad Name"\ntitle = "X"\ncommands = ["hz x"]\n', "lowercase words joined by dashes"),
        ('[[cli]]\nname = "x"\ntitle = "X"\n', "needs `commands`"),
        (
            '[[ui]]\nname = "x"\ntitle = "X"\n[[ui.steps]]\naction = "goto"\ntarget = "/"\n',
            "needs at least one `screenshot`",
        ),
        ('[[ui]]\nname = "x"\ntitle = "X"\n[[ui.steps]]\naction = "dance"\n', "is malformed"),
        (
            (
                '[[cli]]\nname = "x"\ntitle = "X"\ncommands = ["hz x"]\n'
                '[[cli]]\nname = "x"\ntitle = "Y"\ncommands = ["hz y"]\n'
            ),
            "two scenes are named",
        ),
    ],
)
def test_scene_files_are_checked(toml, problem):
    import tomllib

    with pytest.raises(HeinzelError, match=problem):
        scenes_from(tomllib.loads(toml))


def test_a_scenes_file_names_a_plugin_that_isnt_installed_and_the_model_says_so(kernel, tmp_path):
    scenes = tmp_path / "scenes.toml"
    scenes.write_text('[scenes]\nplugin = "heinzel-rain"\n[[cli]]\nname = "a"\ntitle = "A"\ncommands = ["hz a"]\n')
    docs = kernel.resolve(DocsService).documentation(plugin="weather", scenes=scenes)
    assert docs.pages[0].scenes[0].derived
    assert docs.warnings == [
        f"{scenes} names the plugin 'heinzel-rain', which isn't installed (installed: weather); its scenes weren't used"
    ]


def test_a_scenes_file_may_name_its_plugin():
    found = scenes_from(
        {"scenes": {"plugin": "Heinzel-Weather"}, "cli": [{"name": "a", "title": "A", "commands": ["hz a"]}]}
    )
    assert list(found) == ["heinzel-weather"]


def test_an_unknown_plugin_is_an_error(kernel):
    with pytest.raises(HeinzelError, match="No plugin 'rain' is installed"):
        kernel.resolve(DocsService).documentation(plugin="rain")


def test_the_page_renders_every_section(documentation):
    (page,) = documentation.pages
    text = _render.page(page)
    for heading in (
        "# heinzel-weather",
        "## See it",
        "## Themes",
        "## Operations",
        "## Settings",
        "## Credentials",
        "## Capabilities served",
        "## Playbooks",
        "## Jobs",
        "## Setup and health",
        "## Also declares",
    ):
        assert heading in text, heading
    assert f'| `city` | string | `"Cologne"` | `{env_prefix("weather")}CITY` |' in text
    assert "Not recorded yet" in text
    assert "Raises" not in text
    svg = _render.swatches(page.themes[0])
    assert svg.startswith("<svg")
    assert 'aria-label="Weather: colours in light and dark mode"' in svg
    assert svg.count("<rect") == 2 * len(_render.SWATCH_TOKENS)


def test_recordings_beside_the_page_are_embedded(kernel, tmp_path):
    assets = tmp_path / "heinzel-weather" / "assets"
    assets.mkdir(parents=True)
    (assets / "cli.gif").write_bytes(b"GIF89a")
    (assets / "cli.png").write_bytes(b"\x89PNG")
    (assets / "cli.mp4").write_bytes(b"")
    (assets / "cli.md").write_text("```\n$ hz weather today\nSunny\n```\n", encoding="utf-8")
    docs = kernel.resolve(DocsService).documentation(plugin="weather", assets=tmp_path)
    (page,) = docs.pages
    (recording,) = page.recordings
    assert (recording.animation, recording.still, recording.video) == (
        "assets/cli.gif",
        "assets/cli.png",
        "assets/cli.mp4",
    )
    text = _render.page(page)
    assert '<source srcset="assets/cli.png" media="(prefers-reduced-motion: reduce)" />' in text
    assert 'alt="weather in the terminal: a terminal running `hz weather --help`' in text
    assert "$ hz weather today\nSunny" in text  # the transcript, not the command list


def test_build_writes_pages_swatches_and_the_gallery_then_check_reports_stale_ones(documentation, tmp_path):
    result = _build.build_pages(documentation, tmp_path, index=True, check=False)
    assert sorted(result.written) == [
        "README.md",
        "heinzel-weather/assets/theme-weather.svg",
        "heinzel-weather/index.md",
    ]
    assert "[`heinzel-weather`](heinzel-weather/index.md)" in (tmp_path / "README.md").read_text(encoding="utf-8")
    again = _build.build_pages(documentation, tmp_path, index=True, check=True)
    assert (again.stale, again.written, again.stale_pages, again.unchanged) == (False, [], [], 3)
    (tmp_path / "heinzel-weather" / "index.md").write_text("stale", encoding="utf-8")
    stale = _build.build_pages(documentation, tmp_path, index=True, check=True)
    assert (stale.stale, stale.written, stale.stale_pages) == (True, [], ["heinzel-weather/index.md"])
    assert stale.next == [f"hz system plugin docs build --directory {tmp_path}"]
    assert (tmp_path / "heinzel-weather" / "index.md").read_text(encoding="utf-8") == "stale"  # check writes nothing


def test_a_missing_directory_is_stale_so_a_fresh_plugin_runs_make_fix_first(documentation, tmp_path):
    result = _build.build_pages(documentation, tmp_path / "none", index=True, check=True)
    assert result.stale
    assert len(result.stale_pages) == 3
    assert result.next == [f"hz system plugin docs build --directory {tmp_path / 'none'}"]


def test_step_text_reads_as_instructions():
    steps = [
        SceneStep(action="goto", target="/"),
        SceneStep(action="click", role="button", target="Weather"),
        SceneStep(action="click", role="link", target="Rain", optional=True),
        SceneStep(action="screenshot", caption="The forecast"),
    ]
    scene = Scene(name="x", title="X", kind="ui", steps=steps)
    assert [_render._step_text(step) for step in scene.steps] == [
        "Open `/`.",
        "Click the button “Weather”.",
        "Click the link “Rain”. (skipped when it isn't there)",
        "The forecast",
    ]
    assert _render._alt(scene) == "the web app showing The forecast"


def test_the_core_is_a_page_when_asked_for(kernel):
    docs = kernel.resolve(DocsService).documentation(builtin=True)
    assert [page.distribution for page in docs.pages] == ["heinzel", "heinzel-weather"]
    core = docs.pages[0]
    assert core.builtin
    assert core.scenes == []  # the core's scenes are declared, never derived
    assert any(module.qualname == "system.plugin.docs" for module in core.modules)
    assert Path(core.install.split()[-1].removeprefix("git+")).name == "heinzel"
