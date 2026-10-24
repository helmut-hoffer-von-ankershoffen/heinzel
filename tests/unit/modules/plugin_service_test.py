import os
import subprocess
import sys
import urllib.error

import pytest

from heinzel import HeinzelError, Kernel, Module, ModuleRecord, Progress
from heinzel.kernel import Installation, Origin
from heinzel.modules.system.plugin import _service
from heinzel.modules.system.plugin._models import PluginSettings
from heinzel.modules.system.plugin._service import PluginService, distributions_in_spec

pytestmark = pytest.mark.unit

PYTHON = f"{sys.version_info.major}.{sys.version_info.minor}"
RECEIPT = """
[tool]
requirements = [
    { name = "heinzel", git = "https://github.com/org/heinzel?tag=v0.1.0" },
    { name = "heinzel-old", git = "https://github.com/org/heinzel-old" },
]
"""


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("git+https://github.com/org/heinzel-x@v1.0.0", {"heinzel-x"}),
        ("git+https://github.com/org/heinzel-x.git", {"heinzel-x"}),
        ("heinzel-x>=1", {"heinzel-x"}),
        ("/path/to/heinzel_x", {"heinzel-x"}),
    ],
)
def test_distributions_in_spec(spec, expected):
    assert expected <= distributions_in_spec(spec)


@pytest.fixture
def tool_env(tmp_path):
    (tmp_path / "uv-receipt.toml").write_text(RECEIPT, encoding="utf-8")
    return tmp_path


def service_for(make_kernel, prefix, runner=None, kernel=None, settings=None):
    """A plugin service whose kernel has one installed plugin, heinzel-old."""
    installation = Installation(Progress(), prefix=prefix, uv="uv", **({"runner": runner} if runner else {}))
    if kernel is None:
        old = ModuleRecord("old", Module("old", "An old plugin."), Origin("heinzel-old", "1.0.0", builtin=False))
        kernel = Kernel(make_kernel().settings, [old])
    return PluginService(kernel, installation, settings or PluginSettings(github_token=None))


def test_add_in_tool_env_reinstalls_with_plugins(make_kernel, tool_env):
    assert service_for(make_kernel, tool_env).add_command("git+https://github.com/org/heinzel-new@v1.0.0") == [
        "uv", "tool", "install", "--force", "--python", PYTHON,
        "heinzel @ git+https://github.com/org/heinzel@v0.1.0",
        "--with", "heinzel-old @ git+https://github.com/org/heinzel-old",
        "--with", "git+https://github.com/org/heinzel-new@v1.0.0",
    ]  # fmt: skip


def test_add_replaces_older_copy_of_same_plugin(make_kernel, tool_env):
    command = service_for(make_kernel, tool_env).add_command("git+https://github.com/org/heinzel-old@v2.0.0")
    assert command.count("--with") == 1
    assert command[-1] == "git+https://github.com/org/heinzel-old@v2.0.0"


def test_outside_tool_env_uses_uv_pip(make_kernel, tmp_path):
    service = service_for(make_kernel, tmp_path)
    assert service.add_command("./plugin") == ["uv", "pip", "install", "--python", sys.executable, "./plugin"]
    assert service.remove_command("heinzel-old") == [
        "uv", "pip", "uninstall", "--python", sys.executable, "heinzel-old",
    ]  # fmt: skip


def test_remove_in_tool_env_drops_plugin(make_kernel, tool_env):
    command = service_for(make_kernel, tool_env).remove_command("heinzel_old")
    assert "--with" not in command
    assert command[-1] == "heinzel @ git+https://github.com/org/heinzel@v0.1.0"


def test_remove_unknown_plugin_fails(make_kernel, tool_env):
    with pytest.raises(HeinzelError, match=r"'nope' is not an installed plugin\. Installed plugins: heinzel-old"):
        service_for(make_kernel, tool_env).remove_command("nope")


def test_remove_never_touches_non_plugin_distributions(make_kernel, tmp_path):
    with pytest.raises(HeinzelError, match="'pydantic' is not an installed plugin"):
        service_for(make_kernel, tmp_path).remove_command("pydantic")


def test_refuses_to_remove_heinzel_itself(make_kernel, tool_env):
    with pytest.raises(HeinzelError, match="Heinzelmännchen itself"):
        service_for(make_kernel, tool_env).remove_command("heinzel")


def test_failed_add_reports_uv_output(make_kernel, tmp_path):
    def runner(command):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="uv says no")

    with pytest.raises(HeinzelError, match=r"Failed to add plugin 'x'\.\nuv says no"):
        service_for(make_kernel, tmp_path, runner).add("x")


def test_installed_groups_modules_by_distribution(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True, x=Module("x", "x"), x__y=Module("y", "y"))
    [plugin] = service_for(make_kernel, tmp_path, kernel=kernel).installed()
    assert (plugin.distribution, plugin.version, plugin.modules) == ("heinzel-test", "9.9.9", ["x", "x.y"])


def test_new_pins_the_template_and_passes_answers(make_kernel, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "subprocess.run",
        lambda command, check, env: calls.append((command, env)) or subprocess.CompletedProcess(command, 0),
    )
    monkeypatch.setenv("HZ_CREDENTIAL_EXAMPLE_API_TOKEN", "not-for-copier")
    service = service_for(make_kernel, tmp_path)
    assert service.new(tmp_path / "p", data=["plugin_name=crm"], ref="v1.2.3") == tmp_path / "p"
    [(command, env)] = calls
    assert command == [
        "uv", "tool", "run", "copier", "copy", "--vcs-ref", "v1.2.3", "--data", "plugin_name=crm",
        "gh:helmut-hoffer-von-ankershoffen/heinzel", str(tmp_path / "p"),
    ]  # fmt: skip
    assert "HZ_CREDENTIAL_EXAMPLE_API_TOKEN" not in env  # copier never needs a module's credentials
    assert env["PATH"] == os.environ["PATH"]


def test_new_reports_copier_failures(make_kernel, tmp_path, monkeypatch):
    monkeypatch.setattr("subprocess.run", lambda command, check, env: subprocess.CompletedProcess(command, 1))
    with pytest.raises(HeinzelError, match="Failed to scaffold"):
        service_for(make_kernel, tmp_path).new(tmp_path / "p")


def test_first_party_names_resolve_to_the_checkout_in_development(make_kernel, tmp_path):
    service = service_for(make_kernel, tmp_path)
    spec = service.first_party("clock")
    assert spec is not None
    assert spec.startswith("heinzel-clock @ file://")
    assert spec.endswith("/plugins/heinzel-clock")
    assert service.first_party("unknown") is None


def test_first_party_plugins_resolve_by_distribution_name_too(make_kernel, tmp_path):
    service = service_for(make_kernel, tmp_path)
    spec = service.first_party("heinzel-github")
    assert spec == service.first_party("github")
    assert spec is not None
    assert spec.endswith("/plugins/heinzel-github")
    assert service.add_command("heinzel-atlassian")[-1] == service.first_party("atlassian")


def test_first_party_names_follow_a_git_installed_core(make_kernel, tool_env, monkeypatch):
    from heinzel.modules.system.plugin import _service

    monkeypatch.setattr(_service, "__file__", "/nowhere/a/b/c/d/e/_service.py")
    service = service_for(make_kernel, tool_env)
    assert service.first_party("clock") == (
        "heinzel-clock @ git+https://github.com/org/heinzel@v0.1.0#subdirectory=plugins/heinzel-clock"
    )
    assert service.add_command("clock")[-1] == service.first_party("clock")


def test_first_party_names_default_to_the_release_tag(make_kernel, tmp_path, monkeypatch):
    from heinzel.modules.system.plugin import _service

    monkeypatch.setattr(_service, "__file__", "/nowhere/a/b/c/d/e/_service.py")
    assert service_for(make_kernel, tmp_path).first_party("clock") == (
        f"heinzel-clock @ git+{_service.REPOSITORY}@v{_service.__version__}#subdirectory=plugins/heinzel-clock"
    )


def repository(name, stars, **extra):
    """A repository as GitHub's search API returns it."""
    url = f"https://github.com/someone/{name}"
    return {"name": name, "description": f"The {name} plugin.", "html_url": url, "stargazers_count": stars, **extra}


class FakeGitHub:
    """Stands in for GitHub's search API: records each request and answers with `answer`, or raises it."""

    def __init__(self) -> None:
        self.answer: object = {"items": []}
        self.requests: list[tuple[str, str | None, float]] = []

    def get_json(self, url, token, timeout):
        self.requests.append((url, token.get_secret_value() if token else None, timeout))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def github(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(_service, "get_json", fake.get_json)
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "HZ_SYSTEM_PLUGIN__GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    return fake


def test_search_lists_first_party_first_then_community_plugins_by_stars(make_kernel, tmp_path, github):
    github.answer = {"items": [repository("heinzel-few", 1), repository("heinzel-many", 50)]}
    result = service_for(make_kernel, tmp_path).search()
    assert [(p.name, p.source, p.community, p.stars) for p in result.plugins] == [
        ("heinzel-atlassian", "first-party", False, None),
        ("heinzel-clock", "first-party", False, None),
        ("heinzel-github", "first-party", False, None),
        ("heinzel-zoom", "first-party", False, None),
        ("heinzel-many", "github", True, 50),
        ("heinzel-few", "github", True, 1),
    ]
    assert result.warnings == []


def test_search_tells_how_to_install_each_plugin(make_kernel, tmp_path, github):
    github.answer = {"items": [repository("heinzel-crm", 3)]}
    atlassian, clock, github_, zoom, crm = service_for(make_kernel, tmp_path).search().plugins
    assert (atlassian.short_name, atlassian.install_spec) == ("atlassian", "atlassian")
    assert (github_.short_name, github_.install_spec) == ("github", "github")
    assert (clock.short_name, clock.install_spec) == ("clock", "clock")
    assert (zoom.short_name, zoom.install_spec) == ("zoom", "zoom")
    assert (crm.short_name, crm.install_spec) == ("crm", "git+https://github.com/someone/heinzel-crm")


def test_search_marks_installed_plugins(make_kernel, tmp_path, github):
    github.answer = {"items": [repository("heinzel-old", 3), repository("heinzel-new", 2)]}
    installed = {p.name: p.installed for p in service_for(make_kernel, tmp_path).search().plugins}
    assert installed == {
        "heinzel-atlassian": False,
        "heinzel-clock": False,
        "heinzel-github": False,
        "heinzel-zoom": False,
        "heinzel-old": True,
        "heinzel-new": False,
    }


def test_search_prefers_first_party_plugins_and_skips_archived_repositories(make_kernel, tmp_path, github):
    github.answer = {"items": [repository("heinzel-github", 99), repository("heinzel-gone", 9, archived=True)]}
    plugins = service_for(make_kernel, tmp_path).search().plugins
    assert [(p.name, p.source) for p in plugins] == [
        (name, "first-party") for name in ["heinzel-atlassian", "heinzel-clock", "heinzel-github", "heinzel-zoom"]
    ]


def test_search_filters_first_party_plugins_and_passes_the_text_to_github(make_kernel, tmp_path, github):
    service = service_for(make_kernel, tmp_path)
    assert [p.name for p in service.search("TIME team").plugins] == ["heinzel-clock"]
    assert service.search("calendar").plugins == []
    url, token, timeout = github.requests[-1]
    assert url.startswith("https://api.github.com/search/repositories?q=topic%3Aheinzel-plugin+calendar&")
    assert (token, timeout) == (None, 10)


@pytest.mark.parametrize("variable", ["GH_TOKEN", "GITHUB_TOKEN", "HZ_SYSTEM_PLUGIN__GITHUB_TOKEN"])
def test_search_sends_a_github_token_from_the_environment(make_kernel, tmp_path, github, monkeypatch, variable):
    monkeypatch.setenv(variable, "t0ken")
    settings = make_kernel(builtins=True).resolve(PluginSettings)
    service_for(make_kernel, tmp_path, settings=settings).search()
    assert github.requests[-1][1] == "t0ken"


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (urllib.error.URLError("no route to host"), "no route to host"),
        (TimeoutError("timed out"), "timed out"),
        ({"message": "API rate limit exceeded"}, "'items'"),
        ({"items": "nope"}, "expected a list of repositories, got str"),
    ],
)
def test_search_falls_back_to_first_party_plugins_with_a_warning(make_kernel, tmp_path, github, answer, reason):
    github.answer = answer
    result = service_for(make_kernel, tmp_path).search()
    assert [p.name for p in result.plugins] == ["heinzel-atlassian", "heinzel-clock", "heinzel-github", "heinzel-zoom"]
    assert result.warnings == [f"GitHub could not be searched ({reason}); showing first-party plugins only."]
