import base64
import io
import json
import multiprocessing
import os
import subprocess
import sys
import types
import urllib.error

import pytest

from heinzel import HeinzelError, Kernel, Module, ModuleRecord, Progress, Settings, Theme
from heinzel.kernel import DEFAULT_THEME, Installation, Origin
from heinzel.modules.system.plugin import _service, _sources
from heinzel.modules.system.plugin._models import PluginSettings
from heinzel.modules.system.plugin._receipts import PluginReceipts
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


def test_installed_plugins_keep_repository_visibility_from_install_history(make_kernel, tmp_path):
    service = service_for(make_kernel, tmp_path)
    service._record("add", "heinzel-old", private_repository=True)

    assert service.installed()[0].private_repository is True

    service._record("remove", "heinzel-old")
    assert service.installed()[0].private_repository is None


def test_fresh_inventory_is_explicit_and_does_not_recurse_or_change_loaded_modules(make_kernel, tmp_path, monkeypatch):
    service = service_for(make_kernel, tmp_path)
    cached = service.installed()[0]
    calls = []

    def fresh(*args):
        calls.append(args)
        return json.dumps([cached.model_dump(mode="json") | {"version": "2.0.0"}])

    monkeypatch.setattr(service._installation, "hz", fresh)
    assert service.installed()[0].version == "1.0.0"
    assert calls == []
    assert service.installed(fresh=True)[0].version == "2.0.0"
    assert calls == [("system", "plugin", "list", "--json")]
    assert service.installed()[0].version == "1.0.0"


@pytest.mark.parametrize("action", ["add", "develop"])
def test_replacing_a_source_without_visibility_does_not_inherit_its_private_badge(make_kernel, tmp_path, action):
    service = service_for(make_kernel, tmp_path)
    service._record("add", "heinzel-old", private_repository=True)
    service._record(action, "heinzel-old")
    assert service.installed()[0].private_repository is None


@pytest.mark.parametrize(
    ("name", "url", "expected"),
    [
        ("clock", f"{_service.REPOSITORY}/tree/main/plugins/heinzel-clock", True),
        ("extra", f"{_service.REPOSITORY}.git", True),
        ("clock", f"{_service.REPOSITORY}-contrib", False),
        ("clock", f"{_service.REPOSITORY}-fork", False),
        ("clock", "https://github.com/another-owner/heinzel", False),
        ("clock", "https://github.com.evil.example/helmut-hoffer-von-ankershoffen/heinzel", False),
        ("clock", "https://github.com:8443/helmut-hoffer-von-ankershoffen/heinzel", False),
        ("acme", _service.REFERENCE["acme"][1], False),
        ("clock", None, False),
    ],
)
def test_installed_first_party_classification_uses_exact_repository_not_reserved_name(
    make_kernel, tmp_path, name, url, expected
):
    class Dist:
        version = "1.0.0"
        metadata = types.MappingProxyType({})

        def read_text(self, _filename):
            return json.dumps({"url": url, "vcs_info": {"commit_id": "abc"}}) if url else None

    distribution = f"heinzel-{name}"
    record = ModuleRecord(name, Module(name, "An installed plugin."), Origin(distribution, "1.0.0", builtin=False))
    service = service_for(make_kernel, tmp_path, kernel=Kernel(make_kernel().settings, [record]))
    Dist.name = distribution
    service.distributions = {distribution: Dist()}
    assert service.installed()[0].first_party is expected


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
    def runner(command, environment=None):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="uv says no")

    service = service_for(make_kernel, tmp_path, runner)
    service.pypi = lambda url: pypi_project("heinzel-x", "1.0.0")
    with pytest.raises(HeinzelError, match=r"Failed to add plugin 'heinzel-x'\.\nuv says no"):
        service.add("heinzel-x", confirm=True)
    assert service.history() == []  # nothing recorded that didn't happen


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
    assert service.candidate("heinzel-atlassian").requirement == service.first_party("atlassian")


def test_first_party_names_follow_a_git_installed_core(make_kernel, tool_env, monkeypatch):
    from heinzel.modules.system.plugin import _service

    monkeypatch.setattr(_service, "__file__", "/nowhere/a/b/c/d/e/_service.py")
    service = service_for(make_kernel, tool_env)
    assert service.first_party("clock") == (
        "heinzel-clock @ git+https://github.com/org/heinzel@v0.1.0#subdirectory=plugins/heinzel-clock"
    )
    assert service.candidate("clock").requirement == service.first_party("clock")
    assert service.add_command(service.first_party("clock"))[-1] == service.first_party("clock")


def test_first_party_names_default_to_the_release_tag(make_kernel, tmp_path, monkeypatch):
    from heinzel.modules.system.plugin import _service

    monkeypatch.setattr(_service, "__file__", "/nowhere/a/b/c/d/e/_service.py")
    assert service_for(make_kernel, tmp_path).first_party("clock") == (
        f"heinzel-clock @ git+{_service.REPOSITORY}@v{_service.__version__}#subdirectory=plugins/heinzel-clock"
    )


@pytest.fixture(autouse=True)
def _fresh_contrib_cache():
    _service._CONTRIB_CACHE.clear()


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
        ("heinzel-acme", "first-party", False, None),
        ("heinzel-atlassian", "first-party", False, None),
        ("heinzel-clock", "first-party", False, None),
        ("heinzel-github", "first-party", False, None),
        ("heinzel-google", "first-party", False, None),
        ("heinzel-tailscale", "first-party", False, None),
        ("heinzel-zoom", "first-party", False, None),
        ("heinzel-many", "github", True, 50),
        ("heinzel-few", "github", True, 1),
    ]
    assert result.warnings == ["The heinzel-contrib plugins could not be read from GitHub."]


def test_search_tells_how_to_install_each_plugin(make_kernel, tmp_path, github):
    github.answer = {"items": [repository("heinzel-crm", 3)]}
    acme, atlassian, clock, github_, google, tailscale, zoom, crm = service_for(make_kernel, tmp_path).search().plugins
    assert (acme.short_name, acme.install_spec, acme.url) == ("acme", "acme", _service.REFERENCE["acme"][1])
    assert (atlassian.short_name, atlassian.install_spec) == ("atlassian", "atlassian")
    assert (github_.short_name, github_.install_spec) == ("github", "github")
    assert (google.short_name, google.install_spec) == ("google", "google")
    assert (clock.short_name, clock.install_spec) == ("clock", "clock")
    assert (tailscale.short_name, tailscale.install_spec) == ("tailscale", "tailscale")
    assert (zoom.short_name, zoom.install_spec) == ("zoom", "zoom")
    assert (crm.short_name, crm.install_spec) == ("crm", "git+https://github.com/someone/heinzel-crm")


def test_search_marks_installed_plugins(make_kernel, tmp_path, github):
    github.answer = {"items": [repository("heinzel-old", 3), repository("heinzel-new", 2)]}
    installed = {p.name: p.installed for p in service_for(make_kernel, tmp_path).search().plugins}
    assert installed == {
        "heinzel-acme": False,
        "heinzel-atlassian": False,
        "heinzel-clock": False,
        "heinzel-github": False,
        "heinzel-google": False,
        "heinzel-tailscale": False,
        "heinzel-zoom": False,
        "heinzel-old": True,
        "heinzel-new": False,
    }


def test_search_prefers_first_party_plugins_and_skips_archived_repositories(make_kernel, tmp_path, github):
    github.answer = {"items": [repository("heinzel-github", 99), repository("heinzel-gone", 9, archived=True)]}
    plugins = service_for(make_kernel, tmp_path).search().plugins
    assert [(p.name, p.source) for p in plugins] == [
        (name, "first-party")
        for name in [
            "heinzel-acme",
            "heinzel-atlassian",
            "heinzel-clock",
            "heinzel-github",
            "heinzel-google",
            "heinzel-tailscale",
            "heinzel-zoom",
        ]
    ]


def test_search_filters_first_party_plugins_and_passes_the_text_to_github(make_kernel, tmp_path, github):
    service = service_for(make_kernel, tmp_path)
    assert [p.name for p in service.search("TIME team").plugins] == ["heinzel-clock"]
    assert service.search("invoicing").plugins == []
    url, token, timeout = next(r for r in reversed(github.requests) if "/search/" in r[0])
    assert url.startswith("https://api.github.com/search/repositories?q=topic%3Aheinzel-plugin+invoicing&")
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
    assert [p.name for p in result.plugins] == [
        "heinzel-acme",
        "heinzel-atlassian",
        "heinzel-clock",
        "heinzel-github",
        "heinzel-google",
        "heinzel-tailscale",
        "heinzel-zoom",
    ]
    assert result.warnings == [
        f"GitHub could not be searched ({reason}); showing first-party plugins only.",
        "The heinzel-contrib plugins could not be read from GitHub.",
    ]


def test_a_ref_with_slashes_or_a_subdirectory_still_names_the_plugin_it_replaces(make_kernel, tool_env):
    assert distributions_in_spec("git+https://github.com/org/heinzel-foo@feat/x") == {"heinzel-foo"}
    monorepo = "git+https://github.com/org/mono@feat/x#subdirectory=plugins/heinzel-old"
    assert distributions_in_spec(monorepo) == {"heinzel-old"}
    command = service_for(make_kernel, tool_env).add_command(monorepo)
    assert command.count("--with") == 1  # the older heinzel-old is replaced, not listed twice
    assert command[-1] == monorepo


# What `add` shows before installing, and what it installs once confirmed (ADR 0047).


def pypi_project(name, *versions, author="Ada Example", yanked=()):
    """PyPI's JSON API answer for a project with `versions`, the last one latest."""
    return {
        "info": {"name": name, "version": versions[-1], "summary": f"The {name} plugin.", "author": author},
        "releases": {v: [{"yanked": v in yanked}] for v in versions},
    }


class FakeGitHubApi:
    """The GitHub API as `gh api` would answer for one repository, by path."""

    def __init__(self, *, private=False, name="heinzel-crm", version="1.2.0", subdirectory=None):
        pyproject = f'[project]\nname = "{name}"\nversion = "{version}"\ndescription = "CRM notes."\n'
        pyproject += 'authors = [{ name = "Ada Example" }]\n'
        content = {"content": base64.b64encode(pyproject.encode()).decode()}
        where = f"{subdirectory}/pyproject.toml" if subdirectory else "pyproject.toml"
        base = "repos/acme-example/heinzel-crm"
        self.answers = {
            base: {
                "default_branch": "main",
                "private": private,
                "owner": {"login": "acme-example"},
                "html_url": "https://github.com/acme-example/heinzel-crm",
                "description": "A CRM.",
            },
            f"{base}/commits/main": {"sha": "a" * 40},
            f"{base}/commits/v1.2.0": {"sha": "b" * 40},
            f"{base}/contents/{where}?ref={'a' * 40}": content,
            f"{base}/contents/{where}?ref={'b' * 40}": content,
        }
        self.paths: list[str] = []

    def __call__(self, path):
        self.paths.append(path)
        if path not in self.answers:
            msg = f"HTTP 404: Not Found ({path})"
            raise LookupError(msg)
        return self.answers[path]


def installing(make_kernel, prefix, monkeypatch, gh=None):
    """A service whose uv runs are recorded and whose fresh `hz system plugin list` answers nothing new."""
    runs = []

    def runner(command, environment=None):
        runs.append((command, environment))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(_sources.shutil, "which", lambda name: gh if name == "gh" else None)
    service = service_for(make_kernel, prefix, runner)
    monkeypatch.setattr(service._installation, "hz", lambda *args: "[]")
    return service, runs


def _hold_receipt_install_lock(directory, acquired, release):
    with PluginReceipts(directory).installing():
        acquired.set()
        release.wait(10)


def raising(exc):
    def get(url):
        raise exc

    return get


def test_without_confirm_add_installs_nothing_and_shows_a_pinned_pypi_release(make_kernel, tool_env, monkeypatch):
    service, runs = installing(make_kernel, tool_env, monkeypatch)
    service.pypi = lambda url: pypi_project("heinzel-crm", "1.0.0", "1.1.0", "2.0.0", yanked={"2.0.0"})
    change = service.add("heinzel-crm>=1,<3")
    assert (change.done, change.restart_required, runs) == (False, False, [])
    candidate = change.candidate
    assert (candidate.requirement, candidate.version) == ("heinzel-crm==1.1.0", "1.1.0")
    assert (candidate.publisher, candidate.source, candidate.first_party) == ("Ada Example", "pypi", False)
    assert candidate.url == "https://pypi.org/project/heinzel-crm/"
    assert change.operation_id is not None
    assert change.next == [f"hz system plugin add heinzel-crm==1.1.0 --confirm --operation-id {change.operation_id}"]
    assert service.history() == []


def test_a_specifier_no_release_matches_is_refused(make_kernel, tool_env, monkeypatch):
    service, _ = installing(make_kernel, tool_env, monkeypatch)
    service.pypi = lambda url: pypi_project("heinzel-crm", "1.0.0")
    with pytest.raises(HeinzelError, match="No release of heinzel-crm on PyPI matches >=2"):
        service.candidate("heinzel-crm>=2")


def test_confirmed_add_installs_exactly_what_was_shown_and_records_it(make_kernel, tool_env, monkeypatch):
    service, runs = installing(make_kernel, tool_env, monkeypatch)
    service.pypi = lambda url: pypi_project("heinzel-crm", "1.1.0")
    change = service.add("heinzel-crm", confirm=True)
    assert (change.done, change.restart_required, change.action) == (True, True, "add")
    [(command, environment)] = runs
    assert command[-2:] == ["--with", "heinzel-crm==1.1.0"]
    assert environment is None  # no gh: nothing to tell git
    [event] = service.history()
    assert (event.action, event.distribution, event.requirement, event.source, event.version, event.publisher) == (
        "add",
        "heinzel-crm",
        "heinzel-crm==1.1.0",
        "pypi",
        "1.1.0",
        "Ada Example",
    )


def test_preview_receipt_binds_candidate_and_duplicate_confirmation_returns_durable_result(
    make_kernel, tool_env, monkeypatch
):
    service, runs = installing(make_kernel, tool_env, monkeypatch)
    service.pypi = lambda url: pypi_project("heinzel-crm", "1.1.0")
    preview = service.add("heinzel-crm")
    assert preview.operation_id
    assert preview.candidate
    assert preview.candidate
    assert service.operation(preview.operation_id).status == "previewed"
    with pytest.raises(HeinzelError, match="different preview"):
        service.add("heinzel-other", confirm=True, operation_id=preview.operation_id)
    monkeypatch.setattr(
        service,
        "_changed",
        lambda action, distribution, candidate: preview.model_copy(
            update={"done": True, "restart_required": True, "operation_id": preview.operation_id}
        ),
    )

    result = service.add(preview.candidate.requirement, confirm=True, operation_id=preview.operation_id)
    again = service.add(preview.candidate.requirement, confirm=True, operation_id=preview.operation_id)
    reloaded = service_for(make_kernel, tool_env)

    assert len(runs) == 1
    assert result.done
    assert again == result
    assert reloaded.operation(preview.operation_id).status == "completed"
    assert reloaded.operation(preview.operation_id).result == result


def test_receipt_marked_unknown_after_owner_process_exits(make_kernel, tool_env, monkeypatch):
    service, _ = installing(make_kernel, tool_env, monkeypatch)
    service.pypi = lambda url: pypi_project("heinzel-crm", "1.1.0")
    preview = service.add("heinzel-crm")
    assert preview.operation_id
    assert preview.candidate
    receipt = service._receipts.claim(preview.operation_id)
    assert receipt.status == "running"
    path = service._receipts._path(preview.operation_id)
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["owner_pid"] = 2**30
    path.write_text(json.dumps(stored), encoding="utf-8")

    assert service.operation(preview.operation_id).status == "unknown"
    with pytest.raises(HeinzelError, match="unknown result"):
        service.add(preview.candidate.requirement, confirm=True, operation_id=preview.operation_id)


def test_receipt_install_lock_is_shared_between_processes(tmp_path):
    context = multiprocessing.get_context("fork")
    acquired = context.Event()
    release = context.Event()
    process = context.Process(target=_hold_receipt_install_lock, args=(tmp_path, acquired, release))
    process.start()
    try:
        assert acquired.wait(5)
        with (
            pytest.raises(HeinzelError, match="Another plugin installation is running"),
            PluginReceipts(tmp_path).installing(),
        ):
            pass
    finally:
        release.set()
        process.join(10)
    assert process.exitcode == 0


def test_a_github_repository_is_pinned_to_the_commit_its_ref_points_at(make_kernel, tool_env, monkeypatch):
    service, _ = installing(make_kernel, tool_env, monkeypatch)
    service.github_api = FakeGitHubApi(private=True)
    candidate = service.candidate("git+https://github.com/acme-example/heinzel-crm@v1.2.0")
    assert candidate.requirement == f"heinzel-crm @ git+https://github.com/acme-example/heinzel-crm@{'b' * 40}"
    assert (candidate.distribution, candidate.version, candidate.revision) == ("heinzel-crm", "1.2.0", "b" * 40)
    assert (candidate.publisher, candidate.private, candidate.summary) == (
        "acme-example (Ada Example)",
        True,
        "CRM notes.",
    )
    assert candidate.warnings == []


def test_a_plain_https_url_and_a_subdirectory_work_too(make_kernel, tool_env, monkeypatch):
    service, _ = installing(make_kernel, tool_env, monkeypatch)
    service.github_api = FakeGitHubApi(subdirectory="plugins/heinzel-crm")
    candidate = service.candidate("https://github.com/acme-example/heinzel-crm#subdirectory=plugins/heinzel-crm")
    assert candidate.requirement == (
        f"heinzel-crm @ git+https://github.com/acme-example/heinzel-crm@{'a' * 40}#subdirectory=plugins/heinzel-crm"
    )


def test_a_missing_repository_ref_or_package_is_refused_before_anything_is_installed(
    make_kernel, tool_env, monkeypatch
):
    service, runs = installing(make_kernel, tool_env, monkeypatch)
    service.github_api = FakeGitHubApi()
    with pytest.raises(HeinzelError, match="gh auth login"):
        service.add("git+https://github.com/acme-example/heinzel-crm@v9", confirm=True)
    service.pypi = raising(urllib.error.HTTPError("https://pypi.org", 404, "Not Found", {}, io.BytesIO()))  # type: ignore[arg-type]
    with pytest.raises(HeinzelError, match="PyPI has no project 'heinzel-nope'"):
        service.add("heinzel-nope", confirm=True)
    assert runs == []


def test_another_git_host_is_installed_as_given_with_a_warning(make_kernel, tool_env, monkeypatch):
    service, _ = installing(make_kernel, tool_env, monkeypatch)
    candidate = service.candidate("git+https://git.example.org/team/heinzel-notes@v1")
    assert candidate.requirement == "git+https://git.example.org/team/heinzel-notes@v1"
    assert (candidate.version, candidate.publisher) == (None, None)
    assert "git.example.org isn't GitHub" in candidate.warnings[0]


def test_unreachable_pypi_leaves_the_source_unverified_and_says_so(make_kernel, tool_env, monkeypatch):
    service, _ = installing(make_kernel, tool_env, monkeypatch)
    service.pypi = raising(urllib.error.URLError("offline"))
    candidate = service.candidate("heinzel-crm")
    assert (candidate.requirement, candidate.version) == ("heinzel-crm", None)
    assert candidate.warnings == ["What it is couldn't be read (offline): only the source below is known."]


def test_first_party_plugins_are_published_by_the_maintainers(make_kernel, tool_env, monkeypatch):
    service, _ = installing(make_kernel, tool_env, monkeypatch)
    candidate = service.candidate("github")
    assert (candidate.distribution, candidate.first_party, candidate.publisher) == (
        "heinzel-github",
        True,
        _service.MAINTAINERS,
    )
    assert candidate.source == "path"  # this checkout's plugins/ directory


def test_the_reference_company_is_first_party_from_its_own_repository(make_kernel, tool_env, monkeypatch):
    service, _ = installing(make_kernel, tool_env, monkeypatch)
    calls = []

    def offline(path):
        calls.append(path)
        msg = "offline"
        raise OSError(msg)

    service.github_api = offline
    candidate = service.candidate("acme")
    assert candidate.requirement == f"heinzel-acme @ git+{_service.REFERENCE['acme'][1]}"
    assert (candidate.first_party, candidate.publisher) == (True, _service.MAINTAINERS)
    assert calls == ["repos/helmut-hoffer-von-ankershoffen/heinzel-acme"]


def test_with_gh_git_asks_gh_for_github_credentials_and_no_token_passes_through(make_kernel, tool_env, monkeypatch):
    service, runs = installing(make_kernel, tool_env, monkeypatch, gh="/opt/bin/gh")
    service.github_api = FakeGitHubApi(private=True)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    service.add("git+https://github.com/acme-example/heinzel-crm", confirm=True)
    [(_, environment)] = runs
    assert environment == {
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_2": "credential.https://github.com.helper",
        "GIT_CONFIG_VALUE_2": "!/opt/bin/gh auth git-credential",
    }


def test_one_change_at_a_time(make_kernel, tool_env, monkeypatch):
    service, runs = installing(make_kernel, tool_env, monkeypatch)
    service.pypi = lambda url: pypi_project("heinzel-crm", "1.0.0")
    with service._exclusive(), pytest.raises(HeinzelError, match="Another plugin change is running"):
        service.add("heinzel-crm", confirm=True)
    assert runs == []


def test_remove_records_what_was_removed(make_kernel, tool_env, monkeypatch):
    service, runs = installing(make_kernel, tool_env, monkeypatch)
    change = service.remove("heinzel-old")
    assert (change.action, change.done, change.restart_required, len(runs)) == ("remove", True, True, 1)
    assert change.next == []  # the skill sync of the fresh hz succeeded: nothing to do
    assert [(e.action, e.distribution, e.version) for e in service.history()] == [("remove", "heinzel-old", "1.0.0")]


def test_develop_installs_a_checkout_under_its_project_name(make_kernel, tool_env, monkeypatch, tmp_path):
    service, runs = installing(make_kernel, tool_env, monkeypatch)
    checkout = tmp_path / "work"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text('[project]\nname = "heinzel_Crm"\nversion = "0.1.0"\n', encoding="utf-8")
    change = service.develop(checkout)
    assert (change.action, change.distribution) == ("develop", "heinzel-crm")
    assert runs[0][0][-1] == f"heinzel-crm @ {checkout.resolve().as_uri()}"
    assert [(e.action, e.source, e.version) for e in service.history()] == [("develop", "path", "0.1.0")]
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(HeinzelError, match=r"no pyproject\.toml"):
        service.develop(empty)
    (empty / "pyproject.toml").write_text("[tool.x]\n", encoding="utf-8")
    with pytest.raises(HeinzelError, match="names no project"):
        service.develop(empty)


def test_history_skips_lines_it_cannot_read(make_kernel, tmp_path):
    service = service_for(make_kernel, tmp_path)
    path = service._history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    good = '{"at": "2026-10-24T10:00:00Z", "action": "remove", "distribution": "heinzel-x", "requirement": null, '
    good += '"source": null, "version": "1", "publisher": null}'
    path.write_text(f"torn\n{good}\n", encoding="utf-8")
    assert [e.distribution for e in service.history()] == ["heinzel-x"]


# What `list` tells about each plugin.


def test_installed_tells_what_each_plugin_brings_and_whether_it_is_enabled(make_kernel, tmp_path):
    team = Module("acme-payments", "A team layer.")
    team.layer(parent="acme")
    origin = Origin("heinzel-acme-payments", "1.0.0", builtin=False, url="https://example.org/acme")
    off = ModuleRecord("off", Module("off", "Switched off."), Origin("heinzel-off", "2.0.0", builtin=False))
    kernel = Kernel(
        Settings(disabled_modules=frozenset({"off"})),
        [ModuleRecord("acme-payments", team, origin), off],
        errors={"broken": "ImportError: nope"},
    )
    service = service_for(make_kernel, tmp_path, kernel=kernel)
    service.distributions = {}
    plugins = {p.distribution: p for p in service.installed()}
    payments = plugins["heinzel-acme-payments"]
    assert (payments.enabled, payments.layers[0].parent, payments.homepage) == (
        True,
        "acme",
        "https://example.org/acme",
    )
    assert (plugins["heinzel-off"].enabled, plugins["heinzel-off"].modules) == (False, ["off"])
    assert (plugins["broken"].enabled, plugins["broken"].error) == (False, "ImportError: nope")


VCS = '"vcs_info": {"vcs": "git", "commit_id": "abc"'
URL_ABC = "https://github.com/o/heinzel-x/tree/abc"


@pytest.mark.parametrize(
    ("direct_url", "homepage", "expected"),
    [
        (None, None, ("pypi", "https://pypi.org/project/heinzel-x/", None)),
        (None, "https://github.com/o/heinzel-x", ("pypi", "https://github.com/o/heinzel-x", None)),
        (None, "http://insecure.example.org/x", ("pypi", "https://pypi.org/project/heinzel-x/", None)),
        (
            f'{{"url": "https://github.com/o/heinzel-x.git", {VCS}}}}}',
            None,
            ("git", "https://github.com/o/heinzel-x/tree/abc", "abc"),
        ),
        (
            (
                f'{{"url": "https://ada:t0ken@github.com/o/mono", "subdirectory": "plugins/heinzel-x", '
                f'{VCS}, "requested_revision": "main"}}}}'
            ),
            None,
            ("git", "https://github.com/o/mono/tree/abc/plugins/heinzel-x", "abc"),
        ),
        (
            '{"url": "git+https://github.com/o/heinzel-x", "vcs_info": {"vcs": "git", "requested_revision": "v1"}}',
            None,
            ("git", "https://github.com/o/heinzel-x/tree/v1", None),
        ),
        (
            '{"url": "https://github.com/o/heinzel-x", "vcs_info": {"vcs": "git"}}',
            None,
            ("git", "https://github.com/o/heinzel-x", None),
        ),
        (f'{{"url": "git+ssh://git@github.com/o/heinzel-x.git", {VCS}}}}}', None, ("git", URL_ABC, "abc")),
        (f'{{"url": "git@github.com:o/heinzel-x.git", {VCS}}}}}', None, ("git", URL_ABC, "abc")),
        (
            f'{{"url": "https://ada:t0ken@git.example.org/o/heinzel-x.git", {VCS}}}}}',
            None,
            ("git", "https://git.example.org/o/heinzel-x", "abc"),
        ),
        (f'{{"url": "ssh://git@example.org/o/x", {VCS}}}}}', None, ("git", None, "abc")),
        (
            f'{{"url": "https://gitlab.example.org/o/mono.git", "subdirectory": "heinzel-x", {VCS}}}}}',
            None,
            ("git", "https://gitlab.example.org/o/mono/tree/abc/heinzel-x", "abc"),
        ),
        ('{"url": "file:///src/heinzel%20x", "dir_info": {}}', None, ("path", None, None)),
        ('{"url": "https://example.org/x.whl", "archive_info": {}}', None, (None, "https://example.org/x.whl", None)),
        ("not json", None, (None, None, None)),
    ],
)
def test_where_a_plugin_came_from_is_read_from_its_direct_url(direct_url, homepage, expected):
    class Dist:
        name = "heinzel-x"

        def read_text(self, filename):
            assert filename == "direct_url.json"
            return direct_url

    assert _service._source_of(Dist(), homepage) == expected
    assert _service._source_of(None) == (None, None, None)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("git+https://github.com/o/heinzel-x", "https://github.com/o/heinzel-x"),
        ("heinzel-x @ git+https://github.com/o/heinzel-x.git@v1", "https://github.com/o/heinzel-x"),
        (
            "git+https://github.com/o/mono@feat/x#subdirectory=plugins/heinzel-x",
            "https://github.com/o/mono/tree/feat/x/plugins/heinzel-x",
        ),
        (
            "git+https://github.com/o/mono#subdirectory=plugins/heinzel-x",
            "https://github.com/o/mono/tree/HEAD/plugins/heinzel-x",
        ),
        ("git+https://ada:t0ken@git.example.org:8443/o/x", "https://git.example.org:8443/o/x"),
        ("git+ssh://git@github.com/o/x", None),
        ("heinzel-x==1.0", None),
        ("clock", None),
    ],
)
def test_the_repository_a_spec_names_as_a_browser_opens_it(spec, expected):
    assert _sources.repository_url(spec) == expected


def test_search_entries_carry_their_repository_url(make_kernel, tmp_path, github):
    github.answer = {"items": [repository("heinzel-crm", 3)]}
    plugins = {p.name: p for p in service_for(make_kernel, tmp_path).search().plugins}
    assert plugins["heinzel-crm"].repository_url == "https://github.com/someone/heinzel-crm"
    assert plugins["heinzel-clock"].repository_url == f"{_service.REPOSITORY}/tree/main/plugins/heinzel-clock"
    assert plugins["heinzel-acme"].repository_url == _service.REFERENCE["acme"][1]


@pytest.mark.parametrize("visibility", [True, False, None, "false", 0])
@pytest.mark.parametrize("monorepo", [False, True])
def test_catalog_visibility_is_reported_only_for_boolean_github_metadata(make_kernel, tmp_path, visibility, monorepo):
    service = service_for(make_kernel, tmp_path)
    service._monorepo_plugins = lambda _repository: [("heinzel-tools", "plugins/tools", "Tools.")] if monorepo else []
    [plugin] = service._community_plugins(repository("heinzel-tools", 0, private=visibility), set())
    assert plugin.private_repository is (visibility if isinstance(visibility, bool) else None)


def test_installed_plugins_say_where_they_came_from(make_kernel, tmp_path):
    class Dist:
        name = "heinzel-crm"
        version = "1.1.0"
        metadata = types.MappingProxyType({"Summary": "CRM notes."})

        def read_text(self, filename):
            return '{"url": "git+ssh://git@github.com/acme-example/heinzel-crm.git", "vcs_info": {"commit_id": "abc"}}'

    # The kernel read 1.0.0 when it started; 1.1.0 was installed since.
    record = ModuleRecord("crm", Module("crm", "CRM."), Origin("heinzel-crm", "1.0.0", builtin=False))
    service = service_for(make_kernel, tmp_path, kernel=Kernel(make_kernel().settings, [record]))
    service.distributions = {"heinzel-crm": Dist()}
    [plugin] = service.installed()
    assert (plugin.version, plugin.source_kind, plugin.source_url, plugin.revision, plugin.summary) == (
        "1.1.0",
        "git",
        "https://github.com/acme-example/heinzel-crm/tree/abc",
        "abc",
        "CRM notes.",
    )
    assert not plugin.first_party


def _listed(make_kernel, tmp_path, *modules):
    """`installed()` for plugins of one module each, `heinzel-<module name>`."""
    records = [ModuleRecord(m.name, m, Origin(f"heinzel-{m.name}", "1.0.0", builtin=False)) for m in modules]
    service = service_for(make_kernel, tmp_path, kernel=Kernel(Settings(), records))
    service.distributions = {}
    return {p.name: p for p in service.installed()}


def _theme(name):
    return Theme.model_validate(DEFAULT_THEME.model_dump() | {"name": name, "title": name.title()})


@pytest.mark.parametrize(
    ("flags", "kinds"),
    [
        ({}, []),
        ({"capabilities": True}, ["connector"]),
        ({"themes": True, "playbooks": True}, ["theme", "playbook"]),
        ({"playbooks": True, "features": True, "capabilities": True}, ["connector", "capability", "playbook"]),
        (
            {"playbooks": True, "features": True, "capabilities": True, "themes": True},
            ["connector", "theme", "capability", "playbook"],
        ),
    ],
)
def test_kinds_come_in_one_order_whatever_a_plugin_brings(flags, kinds):
    assert (
        _service.plugin_kinds(
            **({"capabilities": False, "themes": False, "features": False, "playbooks": False} | flags)
        )
        == kinds
    )


def test_what_kind_of_plugin_each_one_is(make_kernel, tmp_path):
    clock = Module("clock", "Times.")

    @clock.operation
    def info() -> str:
        """About the plugin."""
        return "clock"

    @clock.operation(name="clock")
    def now() -> str:
        """What time it is."""
        return "now"

    github = Module("github", "GitHub.")
    github.provides("tracker", modes={"read"})(object)

    @github.operation(name="info")
    def about() -> str:
        """About the plugin."""
        return "github"

    acme = Module("acme", "Acme.")
    acme.theme(_theme("acme-night"))
    acme.theme(_theme("acme"))
    acme.playbook("How Acme works.", setup=False)
    plugins = _listed(make_kernel, tmp_path, clock, github, acme)
    assert {name: (p.kinds, p.themes) for name, p in plugins.items()} == {
        "clock": (["capability"], []),
        "github": (["connector"], []),
        "acme": (["theme", "playbook"], ["acme", "acme-night"]),
    }


@pytest.mark.parametrize(
    ("name", "spec", "problem"),
    [
        ("heinzel", "git+https://github.com/acme-example/heinzel-crm", "calls itself heinzel"),
        ("heinzel-crm[all]", "git+https://github.com/acme-example/heinzel-crm", "no valid name"),
        ("heinzel crm", "git+https://github.com/acme-example/heinzel-crm", "no valid name"),
        ("heinzel-github", "git+https://github.com/acme-example/heinzel-crm", "name of a first-party plugin"),
        ("heinzel-other", "heinzel-crm @ git+https://github.com/acme-example/heinzel-crm", "names heinzel-other"),
    ],
)
def test_a_repository_naming_itself_wrongly_is_refused(make_kernel, tool_env, monkeypatch, name, spec, problem):  # noqa: PLR0913, PLR0917 - fixtures and columns
    service, runs = installing(make_kernel, tool_env, monkeypatch)
    service.github_api = FakeGitHubApi(name=name)
    with pytest.raises(HeinzelError, match=problem):
        service.add(spec, confirm=True)
    assert runs == []


def test_a_pypi_package_with_a_first_party_name_or_extras_is_refused(make_kernel, tool_env, monkeypatch):
    service, _ = installing(make_kernel, tool_env, monkeypatch)
    service.pypi = lambda url: pypi_project("heinzel-github", "1.0.0")
    with pytest.raises(HeinzelError, match=r"by name \(github\)"):
        service.candidate("heinzel-github==1.0.0")
    service.pypi = raising(urllib.error.URLError("offline"))
    with pytest.raises(HeinzelError, match="without extras or environment markers"):
        service.candidate("heinzel-crm[all]")
    with pytest.raises(HeinzelError, match="without extras or environment markers"):
        service.candidate('heinzel-crm; python_version > "3"')


def test_a_plugin_not_named_heinzel_something_comes_with_a_warning(make_kernel, tool_env, monkeypatch):
    service, _ = installing(make_kernel, tool_env, monkeypatch)
    service.github_api = FakeGitHubApi(name="crm-tools")
    candidate = service.candidate("git+https://github.com/acme-example/heinzel-crm")
    assert candidate.warnings == ["It is named crm-tools, not heinzel-<name> as plugins are (ADR 0025)."]


def test_metadata_never_carries_control_characters(make_kernel, tool_env, monkeypatch):
    service, _ = installing(make_kernel, tool_env, monkeypatch)
    project = pypi_project("heinzel-crm", "1.0.0", author="Ada\x1b[2K\x1b[1AExample")
    project["info"]["summary"] = "CRM\x07 notes\x9b"
    service.pypi = lambda url: project
    candidate = service.candidate("heinzel-crm")
    assert (candidate.publisher, candidate.summary) == ("Ada[2K[1AExample", "CRM notes")


def contents(text):
    """A file as GitHub's contents API returns it."""
    import base64

    return {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}


class MonorepoGitHub(FakeGitHub):
    """Answers the search with one monorepo and its contents by URL suffix; anything else is a 404."""

    def __init__(self, repo, files) -> None:
        super().__init__()
        self.answer = {"items": [repo]}
        self.files = files

    def get_json(self, url, token, timeout):
        if "/contents/" in url:
            self.requests.append((url, None, timeout))
            try:
                return self.files[url.split("/contents/")[1]]
            except KeyError:
                msg = "404"
                raise OSError(msg) from None
        return super().get_json(url, token, timeout)


def monorepo(monkeypatch, files):
    repo = repository("heinzel-contrib", 4) | {"full_name": "someone/heinzel-contrib", "default_branch": "main"}
    fake = MonorepoGitHub(repo, files)
    monkeypatch.setattr(_service, "get_json", fake.get_json)
    return fake


EXPECTED_SPEC = "heinzel-weather @ git+https://github.com/someone/heinzel-contrib#subdirectory=plugins/heinzel-weather"


def test_search_lists_each_plugin_of_a_monorepo_from_its_manifest(make_kernel, tmp_path, monkeypatch):
    manifest = """
[[plugin]]
name = "weather"
path = "plugins/heinzel-weather"
summary = "Weather where you are."

[[plugin]]
name = "heinzel-example"
summary = "An example."
"""
    monorepo(monkeypatch, {"heinzel-plugins.toml": contents(manifest)})
    community = [p for p in service_for(make_kernel, tmp_path).search().plugins if p.community]
    assert [(p.name, p.description, p.stars) for p in community] == [
        ("heinzel-example", "An example.", 4),
        ("heinzel-weather", "Weather where you are.", 4),
    ]
    assert community[1].install_spec == EXPECTED_SPEC
    assert community[0].install_spec.endswith("#subdirectory=plugins/heinzel-example")
    assert community[1].url == "https://github.com/someone/heinzel-contrib/tree/main/plugins/heinzel-weather"


def test_search_discovers_monorepo_plugins_without_a_manifest(make_kernel, tmp_path, monkeypatch):
    def pyproject(name):
        return contents(f'[project]\nname = "{name}"\ndescription = "About {name}."\n')

    monorepo(
        monkeypatch,
        {
            "plugins": [
                {"name": "heinzel-weather", "type": "dir"},
                {"name": "tools", "type": "dir"},
                {"name": "x", "type": "file"},
            ],
            "plugins/heinzel-weather/pyproject.toml": pyproject("heinzel-weather"),
            "plugins/tools/pyproject.toml": pyproject("tools"),
        },
    )
    community = [p for p in service_for(make_kernel, tmp_path).search().plugins if p.community]
    assert [(p.name, p.description, p.install_spec) for p in community] == [
        ("heinzel-weather", "About heinzel-weather.", EXPECTED_SPEC)
    ]


def test_search_keeps_a_single_plugin_repository_as_one_entry(make_kernel, tmp_path, monkeypatch):
    monorepo(monkeypatch, {})
    community = [p for p in service_for(make_kernel, tmp_path).search().plugins if p.community]
    assert [(p.name, p.install_spec) for p in community] == [
        ("heinzel-contrib", "git+https://github.com/someone/heinzel-contrib")
    ]


def test_a_first_party_preview_confirms_by_name_not_by_its_git_requirement(make_kernel, tool_env, monkeypatch):
    from heinzel.modules.system.plugin._render import render_change

    service, runs = installing(make_kernel, tool_env, monkeypatch)
    preview = service.add("clock")
    assert preview.candidate is not None
    assert preview.candidate.requirement != "clock"  # a git or path requirement ...
    assert preview.operation_id
    assert preview.next == [
        f"hz system plugin add clock --confirm --operation-id {preview.operation_id}"
    ]  # ... which the hint must not suggest
    rendered = render_change(preview)
    from rich.console import Console

    console = Console(record=True, width=200)
    console.print(rendered)
    assert "not reviewed" not in console.export_text()
    assert service.add(preview.candidate.spec, confirm=True, operation_id=preview.operation_id).done
    assert len(runs) == 1


def test_a_community_preview_still_confirms_by_its_requirement_and_warns(make_kernel, tool_env, monkeypatch):
    from rich.console import Console

    from heinzel.modules.system.plugin._render import render_change

    service, _ = installing(make_kernel, tool_env, monkeypatch)
    service.pypi = lambda url: pypi_project("heinzel-crm", "1.1.0")
    preview = service.add("heinzel-crm")
    assert preview.operation_id
    assert preview.next == [f"hz system plugin add heinzel-crm==1.1.0 --confirm --operation-id {preview.operation_id}"]
    console = Console(record=True, width=200)
    console.print(render_change(preview))
    assert "not reviewed" in console.export_text()


def test_a_change_removes_stale_skill_exports_in_the_fresh_hz_and_survives_its_failure(
    make_kernel, tool_env, monkeypatch
):
    service, _ = installing(make_kernel, tool_env, monkeypatch)
    calls = []

    def fresh(*args):
        calls.append(args)
        if args[:3] == ("system", "skill", "export"):
            msg = "no skill module"
            raise HeinzelError(msg)
        return "[]"

    monkeypatch.setattr(service._installation, "hz", fresh)
    change = service.remove("heinzel-old")
    assert ("system", "skill", "export", "--sync", "--json") in calls
    assert change.done
    assert change.next == ["hz system skill export --sync"]


def test_search_lists_the_contrib_monorepo_without_the_topic(make_kernel, tmp_path, monkeypatch):
    manifest = """
[[plugin]]
name = "weather"
path = "plugins/heinzel-weather"
summary = "Weather where you are."

[[plugin]]
name = "example"
path = "plugins/heinzel-example"
summary = "An example."
"""
    monorepo(monkeypatch, {"heinzel-plugins.toml": contents(manifest)}).answer = {"items": []}
    service = service_for(make_kernel, tmp_path)
    contrib = [p for p in service.search().plugins if p.source == "contrib"]
    base = "git+https://github.com/helmut-hoffer-von-ankershoffen/heinzel-contrib"
    assert [(p.name, p.community, p.installed) for p in contrib] == [
        ("heinzel-example", True, False),
        ("heinzel-weather", True, False),
    ]
    assert contrib[1].install_spec == f"heinzel-weather @ {base}@main#subdirectory=plugins/heinzel-weather"
    assert contrib[1].url.endswith("/heinzel-contrib/tree/main/plugins/heinzel-weather")
    assert [p.name for p in service.search("weather").plugins if p.source == "contrib"] == ["heinzel-weather"]


def test_search_does_not_list_contrib_twice_when_it_has_the_topic(make_kernel, tmp_path, monkeypatch):
    manifest = '[[plugin]]\nname = "weather"\npath = "plugins/heinzel-weather"\nsummary = "W."\n'
    fake = monorepo(monkeypatch, {"heinzel-plugins.toml": contents(manifest)})
    fake.answer = {
        "items": [
            repository("heinzel-contrib", 4)
            | {"full_name": "helmut-hoffer-von-ankershoffen/heinzel-contrib", "default_branch": "main"}
        ]
    }
    names = [(p.name, p.source) for p in service_for(make_kernel, tmp_path).search().plugins if p.community]
    assert names == [("heinzel-weather", "contrib")]


def test_search_warns_when_contrib_cannot_be_read_and_keeps_the_rest(make_kernel, tmp_path, github):
    github.answer = {"items": [repository("heinzel-many", 50)]}
    result = service_for(make_kernel, tmp_path).search()
    assert "The heinzel-contrib plugins could not be read from GitHub." in result.warnings
    assert [p.name for p in result.plugins if p.community] == ["heinzel-many"]


def test_search_reads_contrib_once_while_it_is_cached(make_kernel, tmp_path, monkeypatch):
    manifest = '[[plugin]]\nname = "weather"\npath = "plugins/heinzel-weather"\nsummary = "W."\n'
    fake = monorepo(monkeypatch, {"heinzel-plugins.toml": contents(manifest)})
    fake.answer = {"items": []}
    service = service_for(make_kernel, tmp_path)
    service.search()
    service.search("weather")
    assert len([r for r in fake.requests if "/contents/" in r[0]]) == 1
