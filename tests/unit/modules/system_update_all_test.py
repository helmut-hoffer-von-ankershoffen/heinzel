"""One update for everything: pinned plugins move once a person agrees, and the desktop app comes along.

Offline: uv, PyPI, GitHub and `hz app install` are fakes; nothing is installed and no app is touched.
"""

import inspect
import json
import logging
import threading
from types import SimpleNamespace

import pytest

from heinzel import HeinzelError, Prompter
from heinzel.kernel import PluginRelease, ScriptedPrompter
from heinzel.kernel._head_refresh import HeadRefresh
from heinzel.modules import system
from heinzel.modules.system import _update
from heinzel.modules.system._models import PluginPlan, PluginUpdate, UpdatePlan, UpdateResult
from heinzel.modules.system._plugin_releases import PinnedPlugins, PyPIPluginReleases
from heinzel.modules.system._plugin_standing import standing
from heinzel.modules.system._render import PINNED_HINT, plan_text, render_update
from heinzel.modules.system._update import UpdateService
from tests.unit.modules.system_update_test import (
    REPO,
    FakeReleases,
    FakeUv,
    _bind_target,
    installation,
    isolated_python,  # noqa: F401 - the autouse fixture that isolates the fake interpreter
)

pytestmark = pytest.mark.unit

OLD, NEW = "a" * 40, "b" * 40
PLUGINS = f"{REPO}-plugins"
AT_COMMIT = f'{{ name = "heinzel-acme", git = "{PLUGINS}?subdirectory=plugins%2Fheinzel-acme&rev={OLD}" }}'
AT_VERSION = '{ name = "heinzel-acme", specifier = "==1.0.0" }'


def tool(tmp_path, plugin: str, uv: FakeUv):
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv)
    (target.prefix / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements = [{{ name = "heinzel", git = "{REPO}?rev=v0.2.0" }}, {plugin}]\n', encoding="utf-8"
    )
    _bind_target(target)
    target.installed_versions = lambda names: {n: uv.plugins[n] for n in names if n in uv.plugins}  # type: ignore[method-assign]
    return target


def agree(service: UpdateService, **kwargs):
    """Update as a person who saw the plan just now and agreed to it."""
    return service.update(current="0.2.0", agreed=service.plan(current="0.2.0"), **kwargs)


def revisions(uv: FakeUv, target, revision: dict[str, str]) -> None:
    """Let the fresh hz list the plugin with the commit the last install named."""

    def hz(*args: str) -> str:
        if args[:3] != ("system", "plugin", "list"):
            return f"hz {uv.version}\n"
        last = next((c[-1] for c in reversed(uv.commands) if "@" in c[-1].rsplit("git+", 1)[-1]), "")
        commit = last.rsplit("@", 1)[-1].split("#", 1)[0] if "git+" in last else revision["now"]
        return json.dumps([{"distribution": n, "version": v, "revision": commit} for n, v in uv.plugins.items()])

    uv.hz = hz  # type: ignore[method-assign]
    target.hz = hz  # type: ignore[method-assign]


def newer(version: str = "1.2.0", commit: str | None = NEW):
    return lambda timeout: PinnedPlugins(newer={"heinzel-acme": PluginRelease(version, None, commit=commit)})


def test_a_commit_pinned_plugin_moves_to_its_release_tags_commit_once_agreed(tmp_path):
    uv = FakeUv("0.2.0")
    uv.available = {"heinzel-acme": "1.2.0"}
    target = tool(tmp_path, AT_COMMIT, uv)
    revisions(uv, target, {"now": OLD})
    result = agree(UpdateService(target, FakeReleases("0.4.0"), pinned=newer()))
    assert uv.commands[-1][-1] == f"heinzel-acme @ git+{PLUGINS}@{NEW}#subdirectory=plugins/heinzel-acme"
    [plugin] = result.plugins
    assert (plugin.status, plugin.before, plugin.after, plugin.pinned) == ("upgraded", "1.0.0", "1.2.0", None)


def test_without_agreement_a_commit_pin_stays_and_the_result_says_how_to_move_it(tmp_path):
    uv = FakeUv("0.2.0")
    target = tool(tmp_path, AT_COMMIT, uv)
    revisions(uv, target, {"now": OLD})

    def never(timeout):
        msg = "nobody agreed, so no release is asked"
        raise AssertionError(msg)

    result = UpdateService(target, FakeReleases("0.4.0"), pinned=never).update(current="0.2.0")
    assert uv.commands[-1][-1] == f"heinzel-acme @ git+{PLUGINS}@{OLD}#subdirectory=plugins/heinzel-acme"
    assert result.plugins[0].pinned == OLD
    text = render_update(result).plain
    assert f"kept at 1.0.0 (pinned to {OLD}; newer releases not checked)" in text
    assert PINNED_HINT in text


def test_an_exact_index_pin_follows_the_index_once_agreed(tmp_path):
    uv = FakeUv("0.2.0")
    uv.available = {"heinzel-acme": "1.2.0"}
    target = tool(tmp_path, AT_VERSION, uv)
    service = UpdateService(target, FakeReleases("0.4.0"), pinned=newer(commit=None))
    result = agree(service)
    assert uv.commands[-1][-1] == "heinzel-acme>=1.2.0"  # a floor: it follows the index, and never goes below
    assert (result.plugins[0].status, result.plugins[0].after, result.plugins[0].pinned) == ("upgraded", "1.2.0", None)


def test_an_exact_index_pin_stays_without_agreement(tmp_path):
    uv = FakeUv("0.2.0")
    uv.available = {"heinzel-acme": "1.2.0"}
    target = tool(tmp_path, AT_VERSION, uv)
    result = UpdateService(target, FakeReleases("0.4.0"), pinned=newer()).update(current="0.2.0")
    assert uv.commands[-1][-1] == "heinzel-acme==1.0.0"
    assert (result.plugins[0].status, result.plugins[0].pinned) == ("unchanged", "==1.0.0")


def test_a_pinned_plugin_that_is_the_newest_release_is_already_newest_not_unchecked(tmp_path):
    uv = FakeUv("0.2.0")
    target = tool(tmp_path, AT_COMMIT, uv)
    revisions(uv, target, {"now": OLD})
    service = UpdateService(
        target, FakeReleases("0.4.0"), pinned=lambda timeout: PinnedPlugins(current=["heinzel-acme"])
    )
    result = agree(service)
    assert uv.commands[-1][-1].endswith(f"@{OLD}#subdirectory=plugins/heinzel-acme")
    assert standing(result.plugins[0]) == "already newest (1.0.0)"
    assert PINNED_HINT not in render_update(result).plain


def test_an_exact_pin_at_the_newest_release_follows_the_index_from_now_on(tmp_path):
    uv = FakeUv("0.2.0")
    target = tool(tmp_path, AT_VERSION, uv)
    service = UpdateService(
        target, FakeReleases("0.4.0"), pinned=lambda timeout: PinnedPlugins(current=["heinzel-acme"])
    )
    result = agree(service)
    assert uv.commands[-1][-1] == "heinzel-acme>=1.0.0"
    assert standing(result.plugins[0]) == "already newest (1.0.0)"


def test_releases_that_cannot_be_asked_move_nothing_and_log_why(tmp_path, caplog):
    uv = FakeUv("0.2.0")
    target = tool(tmp_path, AT_VERSION, uv)

    def offline(timeout):
        msg = "network down"
        raise OSError(msg)

    with caplog.at_level(logging.INFO, logger="heinzel.update"):
        result = agree(UpdateService(target, FakeReleases("0.4.0"), pinned=offline))
    assert uv.commands[-1][-1] == "heinzel-acme==1.0.0"
    assert result.plugins[0].pinned == "==1.0.0"
    events = [(r.getMessage(), r.__dict__.get("phase"), r.__dict__.get("outcome")) for r in caplog.records]
    assert ("update.plan", "start", None) in events
    assert ("update.plan", "end", "skipped") in events


def test_planning_logs_one_start_and_one_outcome_with_what_moves(tmp_path, caplog):
    uv = FakeUv("0.2.0")
    uv.available = {"heinzel-acme": "1.2.0"}
    target = tool(tmp_path, AT_VERSION, uv)
    with caplog.at_level(logging.INFO, logger="heinzel.update"):
        UpdateService(target, FakeReleases("0.4.0"), pinned=newer(commit=None)).plan(current="0.2.0")
    asked = [r for r in caplog.records if r.getMessage() == "update.plan"]
    assert [r.__dict__["phase"] for r in asked] == ["start", "end"]
    assert (asked[1].__dict__["outcome"], asked[1].__dict__["moved"]) == ("ok", ["heinzel-acme"])


def test_an_explicit_source_never_moves_a_pin(tmp_path):
    uv = FakeUv("0.2.0")
    target = tool(tmp_path, AT_VERSION, uv)
    service = UpdateService(target, FakeReleases("0.4.0"), pinned=newer())
    plan = service.plan(current="0.2.0")
    assert plan.plugins[0].moves_to == "heinzel-acme>=1.2.0"
    service.update(f"git+{REPO}@v0.3.0", current="0.2.0", agreed=plan)
    assert uv.commands[-1][-1] == "heinzel-acme==1.0.0"


def test_a_plugin_that_blocks_at_its_new_release_is_kept_at_the_commit_it_has(tmp_path):
    uv = FakeUv("0.2.0")
    uv.failures = [
        (
            "error: No solution found when resolving dependencies\n  cause: Because heinzel-acme==1.2.0 depends on "
            "heinzel<0.3 and you require heinzel==0.4.0, we can conclude that your requirements are unsatisfiable."
        )
    ]
    target = tool(tmp_path, AT_COMMIT, uv)
    revisions(uv, target, {"now": OLD})
    result = agree(UpdateService(target, FakeReleases("0.4.0"), pinned=newer()))
    assert uv.commands[0][-1].endswith(f"@{NEW}#subdirectory=plugins/heinzel-acme")
    assert uv.commands[-1][-1] == f"heinzel-acme @ git+{PLUGINS}@{OLD}#subdirectory=plugins/heinzel-acme"
    assert (result.plugins[0].status, result.plugins[0].after) == ("failed", "1.0.0")
    assert result.after == "0.4.0"


# The plan.


def planned(tmp_path, plugin: str, found: PinnedPlugins, **kwargs) -> UpdatePlan:
    uv = FakeUv("0.2.0")
    target = tool(tmp_path, plugin, uv)
    service = UpdateService(target, FakeReleases("0.4.0"), pinned=lambda timeout: found, **kwargs)
    plan = service.plan(current="0.2.0")
    assert uv.commands == []  # a plan only asks
    return plan


def test_the_plan_names_the_core_and_where_a_commit_pin_moves(tmp_path):
    plan = planned(tmp_path, AT_COMMIT, newer()(5.0))
    assert (plan.current, plan.latest, plan.core) == ("0.2.0", "0.4.0", "hz 0.2.0 → 0.4.0.")
    [plugin] = plan.plugins
    assert (plugin.action, plugin.installed, plugin.target) == ("update", "1.0.0", "1.2.0")
    assert plugin.detail == "Pinned to aaaaaaaaaaaa; moves to release 1.2.0, to the commit of its release tag."


def test_the_plan_says_an_exact_pin_follows_the_index_afterwards(tmp_path):
    [plugin] = planned(tmp_path, AT_VERSION, newer(commit=None)(5.0)).plugins
    assert plugin.detail == "Pinned to ==1.0.0; moves to release 1.2.0, then it follows the package index."
    assert plugin.moves_to == "heinzel-acme>=1.2.0"


def test_the_plan_keeps_a_commit_without_release_tags_and_says_so(tmp_path):
    [plugin] = planned(tmp_path, AT_COMMIT, PinnedPlugins()).plugins
    assert plugin.action == "kept"
    assert plugin.detail == "Pinned to aaaaaaaaaaaa, which names no release to follow; an update leaves it as it is."


def test_the_plan_says_why_a_pinned_plugin_could_not_be_asked(tmp_path):
    found = PinnedPlugins(unasked={"heinzel-acme": "GitHub couldn't be asked (rate limit)"})
    [plugin] = planned(tmp_path, AT_COMMIT, found).plugins
    assert (plugin.action, plugin.target) == ("kept", None)
    assert plugin.detail == "Pinned to aaaaaaaaaaaa; stays there: GitHub couldn't be asked (rate limit)."


def test_the_plan_offers_an_index_follower_its_newer_release(tmp_path):
    following = '{ name = "heinzel-acme", specifier = ">=1" }'
    [plugin] = planned(tmp_path, following, PinnedPlugins(), newer=lambda timeout: {"heinzel-acme": "1.3.0"}).plugins
    assert (plugin.action, plugin.target) == ("update", "1.3.0")


def test_a_plan_whose_sources_are_offline_still_answers(tmp_path):
    uv = FakeUv("0.2.0")
    target = tool(tmp_path, AT_COMMIT, uv)

    def offline(timeout):
        msg = "network down"
        raise OSError(msg)

    plan = UpdateService(target, FakeReleases(HeinzelError("offline")), pinned=offline, newer=offline).plan("0.2.0")
    assert plan.latest is None
    assert plan.core == "hz 0.2.0: the newest release couldn't be asked; the update asks again."
    assert plan.plugins[0].action == "kept"
    assert "couldn't be asked (network down)" in plan.plugins[0].detail


def test_the_plan_in_plain_lines():
    plan = UpdatePlan(
        current="0.2.0",
        latest="0.4.0",
        core="hz 0.2.0 → 0.4.0.",
        plugins=[
            PluginPlan(distribution="heinzel-a", installed="1.0.0", target="1.2.0", action="update", detail="Moves."),
            PluginPlan(distribution="heinzel-b", installed="2.0.0", action="current"),
            PluginPlan(distribution="heinzel-c", installed=None, action="kept", detail="Pinned."),
        ],
        desktop=True,
    )
    assert plan_text(plan).splitlines() == [
        "hz 0.2.0 → 0.4.0.",
        "Plugins:",
        "  heinzel-a: 1.0.0 → 1.2.0. Moves.",
        "  heinzel-b: already newest (2.0.0)",
        "  heinzel-c: kept at version unknown. Pinned.",
        "Desktop app: updated afterwards with `hz app install`, if you agree.",
    ]


# The desktop app: a fake `hz app install`, never the real one.


@pytest.fixture
def desktop_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    receipt = home / _update.DESKTOP_RECEIPT
    receipt.parent.mkdir(parents=True)
    receipt.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(_update.platform, "system", lambda: "Darwin")
    return home


def with_desktop(tmp_path, home, app):
    uv = FakeUv("0.2.0")
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv)
    return UpdateService(target, FakeReleases("0.4.0"), home=home, desktop=app)


def test_the_desktop_app_is_left_pending_with_the_hint_unless_asked_for(tmp_path, desktop_home):
    def never():
        msg = "the desktop app was not asked for"
        raise AssertionError(msg)

    result = with_desktop(tmp_path, desktop_home, never).update(current="0.2.0")
    [head] = result.heads
    assert (head.head, head.status) == ("desktop", "pending")
    assert "Update it now with `hz app install`." in result.message


def test_the_desktop_app_is_updated_by_the_new_hz_when_asked_for(tmp_path, desktop_home, caplog):
    ran = []

    def app():
        ran.append("hz app install")
        return 0, "Installed."

    service = with_desktop(tmp_path, desktop_home, app)
    with caplog.at_level(logging.INFO, logger="heinzel.update"):
        result = service.update(current="0.2.0", desktop=True)
    assert ran == ["hz app install"]
    [head] = result.heads
    assert (head.status, head.version, head.detail) == ("verified", "0.4.0", "Updated by `hz app install`.")
    assert result.message.endswith("Desktop app: verified. Updated by `hz app install`.")
    assert "Not everything runs the new version yet" not in result.message
    events = [(r.__dict__.get("phase"), r.__dict__.get("outcome")) for r in caplog.records if r.msg == "update.desktop"]
    assert events == [("start", None), ("end", "ok")]


def test_a_desktop_app_that_cannot_be_updated_never_undoes_the_update(tmp_path, desktop_home, caplog):
    def app():
        return (
            1,
            "Heinzelmaennchen is open, so the app cannot be replaced right now.\nQuit it, then run hz app install.",
        )

    with caplog.at_level(logging.INFO, logger="heinzel.update"):
        result = with_desktop(tmp_path, desktop_home, app).update(current="0.2.0", desktop=True)
    assert (result.updated, result.after) == (True, "0.4.0")
    [head] = result.heads
    assert head.status == "failed"
    assert "is open, so the app cannot be replaced right now. Quit it" in head.detail
    assert head.detail.endswith("hz itself is updated. Run `hz app install` again.")
    ended = [r for r in caplog.records if r.msg == "update.desktop" and r.__dict__.get("phase") == "end"]
    assert [(r.__dict__["outcome"], r.__dict__["exit_code"]) for r in ended] == [("error", 1)]


def test_a_desktop_install_that_cannot_start_is_a_failed_head(tmp_path, desktop_home):
    def app():
        msg = "no interpreter"
        raise OSError(msg)

    result = with_desktop(tmp_path, desktop_home, app).update(current="0.2.0", desktop=True)
    assert result.heads[0].status == "failed"
    assert "no interpreter" in result.heads[0].detail


def test_nothing_newer_leaves_the_desktop_app_alone(tmp_path, desktop_home):
    def never():
        msg = "nothing changed"
        raise AssertionError(msg)

    uv = FakeUv("0.4.0")
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.4.0"', uv)
    service = UpdateService(target, FakeReleases("0.4.0"), home=desktop_home, desktop=never)
    result = service.update(current="0.4.0", desktop=True)
    assert (result.updated, result.heads) == (False, [])


def test_agreeing_afterwards_updates_the_pending_desktop_app_and_rewrites_its_line(tmp_path, desktop_home):
    service = with_desktop(tmp_path, desktop_home, lambda: (0, ""))
    pending = service.update(current="0.2.0")
    result = service.update_desktop(pending)
    assert [head.status for head in result.heads] == ["verified"]
    assert result.message == f"{pending.message.splitlines()[0]}\nDesktop app: verified. Updated by `hz app install`."
    assert service.update_desktop(result) == result  # nothing pending: nothing runs again


def test_the_real_desktop_step_runs_hz_app_install_in_a_fresh_interpreter(monkeypatch, tmp_path):
    ran = []

    def run(command, **kwargs):
        ran.append((command, kwargs))
        return SimpleNamespace(returncode=3, stdout="out", stderr="")

    monkeypatch.setattr(_update.subprocess, "run", run)
    uv = FakeUv("0.2.0")
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv), FakeReleases())
    assert service._app_install() == (3, "out")
    [(command, kwargs)] = ran
    assert command[-4:] == ["-m", "heinzel", "app", "install"]
    assert (kwargs["timeout"], kwargs["stdin"], kwargs["check"]) == (900, _update.subprocess.DEVNULL, False)


# The operation: what a person is asked, and what answers in advance.


class Service:
    """The update service as the operation uses it: records what it was asked to do.

    `targets` are the plugin's newest release at each successive look, so a release can appear between two plans.
    """

    def __init__(self, *, desktop: bool = True, updated: bool = True, targets: tuple[str, ...] = ("1.2.0",)) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.agreed: UpdatePlan | None = None
        self._desktop, self._updated, self._targets = desktop, updated, list(targets)

    def plan(self, *, channel: str) -> UpdatePlan:
        self.calls.append(("plan", {"channel": channel}))
        target = self._targets.pop(0) if len(self._targets) > 1 else self._targets[0]
        plugin = PluginPlan(
            distribution="heinzel-acme",
            installed="1.0.0",
            target=target,
            action="update",
            moves_to=f"heinzel-acme>={target}",
        )
        return UpdatePlan(
            current="0.2.0",
            latest="0.4.0",
            core="hz 0.2.0 → 0.4.0.",
            plugins=[plugin],
            desktop=True,
            token=f"plan-{target}",
        )

    def update(self, source, *, channel: str, agreed: UpdatePlan | None, desktop: bool) -> UpdateResult:
        self.calls.append(("update", {"source": source, "move_pins": agreed is not None, "desktop": desktop}))
        self.agreed = agreed
        heads = []
        if self._desktop and self._updated:
            status = "verified" if desktop else "pending"
            heads = [HeadRefresh(head="desktop", status=status, detail="Update it now with `hz app install`.")]
        return UpdateResult(
            before="0.2.0",
            after="0.4.0",
            latest="0.4.0",
            pinned_to=None,
            updated=self._updated,
            message="Updated hz 0.2.0 → 0.4.0.",
            next=[],
            plugins=[PluginUpdate(distribution="heinzel-acme", status="upgraded", before="1.0.0", after="1.2.0")],
            heads=heads,
        )

    def update_desktop(self, result: UpdateResult) -> UpdateResult:
        self.calls.append(("desktop", {}))
        return result.model_copy(update={"heads": [HeadRefresh(head="desktop", status="verified")]})


KERNEL = SimpleNamespace(
    settings=SimpleNamespace(
        auto_update=False, auto_update_min_age_hours=6, auto_update_plugin_min_age_hours=24, update_channel="stable"
    )
)


def run(service: Service, prompter: Prompter, **kwargs) -> UpdateResult:
    return system.update(service, KERNEL, prompter, **kwargs)  # type: ignore[arg-type]


def names(service: Service) -> list[str]:
    return [name for name, _ in service.calls]


def test_on_a_terminal_the_plan_is_shown_and_enter_agrees_to_everything():
    service, prompter = Service(), ScriptedPrompter(["", ""])
    result = run(service, prompter)
    # The plan is read again after the answer, so what is installed is what was shown.
    assert names(service) == ["plan", "plan", "update", "desktop"]
    assert service.calls[2][1] == {"source": None, "move_pins": True, "desktop": False}
    assert [line for line in prompter.transcript if "[Y/n]" in line] == [
        "Update now? [Y/n] ",
        "Update the desktop app now? [Y/n] ",
    ]
    assert result.plan is not None
    assert service.agreed is not None
    assert service.agreed.token == "plan-1.2.0"
    assert result.heads[0].status == "verified"


def test_on_a_terminal_no_changes_nothing_and_says_so():
    service, prompter = Service(), ScriptedPrompter(["n"])
    result = run(service, prompter)
    assert names(service) == ["plan"]
    assert (result.updated, result.previewed, result.message) == (False, True, "Nothing was changed.")
    assert result.next == ["hz system update --yes"]


def test_on_a_terminal_a_release_that_appears_while_reading_is_shown_and_asked_again():
    service = Service(targets=("1.2.0", "1.3.0"))
    prompter = ScriptedPrompter(["", "", "n"])
    run(service, prompter)
    # Agreed to 1.2.0, but 1.3.0 appeared: the new plan is shown and only that one is installed, once agreed.
    assert names(service) == ["plan", "plan", "plan", "update"]
    assert "The plan changed while it was shown. This is the plan now:" in prompter.transcript
    assert service.agreed is not None
    assert service.agreed.plugins[0].moves_to == "heinzel-acme>=1.3.0"
    assert [line for line in prompter.transcript if line.startswith("Update now?")] == ["Update now? [Y/n] "] * 2


def test_on_a_terminal_a_changed_plan_can_still_be_declined():
    service = Service(targets=("1.2.0", "1.3.0"))
    result = run(service, ScriptedPrompter(["", "n"]))
    assert "update" not in names(service)
    assert (result.previewed, result.message) == (True, "Nothing was changed.")
    assert result.plan is not None
    assert result.plan.token == "plan-1.3.0"


def test_on_a_terminal_a_plan_that_keeps_changing_installs_nothing():
    service = Service(targets=("1.1.0", "1.2.0", "1.3.0", "1.4.0", "1.5.0"))
    result = run(service, ScriptedPrompter(["", "", ""]))
    assert "update" not in names(service)
    assert result.message == "The plan kept changing, so nothing was changed. Try again in a moment."


def test_on_a_terminal_no_to_the_desktop_app_keeps_its_hint():
    service = Service()
    result = run(service, ScriptedPrompter(["y", "n"]))
    assert names(service) == ["plan", "plan", "update"]
    assert (result.heads[0].status, result.heads[0].detail) == ("pending", "Update it now with `hz app install`.")


def test_on_a_terminal_without_a_desktop_app_only_the_update_is_asked():
    service, prompter = Service(desktop=False), ScriptedPrompter([""])
    run(service, prompter)
    assert names(service) == ["plan", "plan", "update"]
    assert prompter.answers == []


def test_yes_agrees_in_advance_to_the_plan_as_it_is_and_asks_nothing():
    service, prompter = Service(), ScriptedPrompter([])
    result = run(service, prompter, yes=True)
    assert service.calls[1:] == [("update", {"source": None, "move_pins": True, "desktop": True})]
    assert names(service) == ["plan", "update"]
    assert result.plan is not None
    assert service.agreed is not None
    assert result.plan.token == service.agreed.token


def test_yes_with_the_token_of_the_shown_plan_installs_exactly_that_plan():
    service = Service()
    run(service, Prompter(), yes=True, plan="plan-1.2.0")
    assert names(service) == ["plan", "update"]
    assert service.agreed is not None
    assert service.agreed.plugins[0].moves_to == "heinzel-acme>=1.2.0"


def test_yes_with_the_token_of_a_plan_that_changed_installs_nothing_and_answers_with_the_new_plan():
    service = Service(targets=("1.3.0",))  # the app showed 1.2.0; 1.3.0 was uploaded before the person agreed
    result = run(service, Prompter(), yes=True, plan="plan-1.2.0")
    assert names(service) == ["plan"]
    assert (result.updated, result.previewed) == (False, True)
    assert result.message == "The plan changed since it was shown, so nothing was changed. This is the plan now."
    assert result.plan is not None
    assert result.plan.token == "plan-1.3.0"


def test_no_desktop_wins_over_yes_and_keep_pins_keeps_them_without_asking_any_release():
    service = Service()
    run(service, ScriptedPrompter([]), yes=True, desktop=False, keep_pins=True)
    assert service.calls == [("update", {"source": None, "move_pins": False, "desktop": False})]


def test_on_a_terminal_keep_pins_still_shows_the_plan_but_moves_none():
    service = Service()
    run(service, ScriptedPrompter(["", "n"]), keep_pins=True)
    assert service.calls[-1] == ("update", {"source": None, "move_pins": False, "desktop": False})


def test_the_desktop_flag_answers_the_second_question_on_a_terminal():
    service, prompter = Service(), ScriptedPrompter([""])
    run(service, prompter, desktop=True)
    assert service.calls[-1] == ("update", {"source": None, "move_pins": True, "desktop": True})
    assert prompter.answers == []


def test_where_nobody_can_answer_nothing_is_asked_pins_stay_and_the_hint_stays():
    service = Service()
    result = run(service, Prompter())  # a script, --json, the REST API
    assert service.calls == [("update", {"source": None, "move_pins": False, "desktop": False})]
    assert result.heads[0].status == "pending"
    assert result.plan is None


def test_a_token_without_yes_agrees_to_nothing():
    service = Service()
    run(service, Prompter(), plan="plan-1.2.0")
    assert service.calls == [("update", {"source": None, "move_pins": False, "desktop": False})]


def test_where_nobody_can_answer_the_desktop_flag_still_counts():
    service = Service()
    run(service, Prompter(), desktop=True)
    assert service.calls == [("update", {"source": None, "move_pins": False, "desktop": True})]


def test_a_preview_only_shows_the_plan_wherever_it_is_asked():
    for prompter in (Prompter(), ScriptedPrompter([])):
        service = Service()
        result = run(service, prompter, preview=True)
        assert names(service) == ["plan"]
        assert (result.previewed, result.updated) == (True, False)
        assert result.plan is not None
        assert result.plan.token == "plan-1.2.0"
        text = render_update(result).plain
        assert text.splitlines()[0] == "hz 0.2.0 → 0.4.0."
        assert text.endswith("This was a preview; nothing was changed.\nTo update: hz system update --yes")


def test_an_explicit_source_is_never_planned_and_keeps_its_question_out():
    service, prompter = Service(), ScriptedPrompter([])
    run(service, prompter, source="git+https://example.test/x@v1")
    assert service.calls == [
        ("update", {"source": "git+https://example.test/x@v1", "move_pins": False, "desktop": False})
    ]


def test_the_yes_option_says_who_agrees_when_no_person_is_asked():
    text = str(inspect.signature(system.update).parameters["yes"].annotation)
    assert "an agent over MCP or a REST client then moves pins" in text
    assert "runs `hz app install` without another question" in text


# An update never lowers a version, the agreement belongs to one call, and a plan always answers.

UPLOADED = {"upload_time_iso_8601": "2026-10-01T08:00:00.000000Z", "yanked": False}
YANKED = UPLOADED | {"yanked": True}


@pytest.mark.parametrize(
    ("installed", "releases"),
    [
        ("1.3.0rc1", {"1.2.0": [UPLOADED], "1.3.0rc1": [UPLOADED]}),  # pinned to a pre-release on purpose
        ("1.3.0", {"1.2.0": [UPLOADED], "1.3.0": [YANKED]}),  # the installed release was yanked
    ],
)
def test_an_exact_pin_newer_than_the_newest_stable_release_is_never_moved_back(tmp_path, installed, releases):
    uv = FakeUv("0.2.0")
    uv.plugins, uv.available = {"heinzel-acme": installed}, {"heinzel-acme": "1.2.0"}  # a bare name would give 1.2.0
    target = tool(tmp_path, f'{{ name = "heinzel-acme", specifier = "=={installed}" }}', uv)
    asked = PyPIPluginReleases(target, lambda url, timeout: {"releases": releases})
    service = UpdateService(target, FakeReleases("0.4.0"), pinned=asked.pinned)
    [plan] = service.plan(current="0.2.0").plugins
    assert (plan.action, plan.target, plan.moves_to) == ("kept", None, None)
    assert plan.detail == (
        f"Pinned to =={installed}, newer than its newest stable release 1.2.0; an update never moves back, so it stays."
    )
    result = agree(service)
    assert uv.commands[-1][-1] == f"heinzel-acme=={installed}"
    assert (result.plugins[0].before, result.plugins[0].after) == (installed, installed)
    assert PINNED_HINT not in render_update(result.model_copy(update={"plan": service.plan(current="0.2.0")})).plain


def test_a_commit_pin_newer_than_its_newest_release_tag_is_kept_and_the_plan_says_so(tmp_path):
    [plugin] = planned(tmp_path, AT_COMMIT, PinnedPlugins(ahead={"heinzel-acme": "0.9.0"})).plugins
    assert (plugin.action, plugin.moves_to) == ("kept", None)
    assert "newer than its newest stable release 0.9.0; an update never moves back" in plugin.detail


def test_a_release_tag_that_names_no_commit_moves_nothing(tmp_path):
    [plugin] = planned(tmp_path, AT_COMMIT, newer(commit=None)(5.0)).plugins
    assert (plugin.action, plugin.target, plugin.moves_to) == ("kept", None, None)
    assert plugin.detail == "Pinned to aaaaaaaaaaaa; its release tag names no commit, so it stays."


def test_the_plans_token_changes_exactly_when_what_it_would_install_changes(tmp_path):
    first = planned(tmp_path / "a", AT_VERSION, newer("1.2.0", None)(5.0))
    same = planned(tmp_path / "b", AT_VERSION, newer("1.2.0", None)(5.0))
    later = planned(tmp_path / "c", AT_VERSION, newer("1.3.0", None)(5.0))
    assert len(first.token) == 32
    assert first.token == same.token
    assert first.token != later.token


def test_the_agreement_belongs_to_one_call_and_is_gone_afterwards(tmp_path):
    uv = FakeUv("0.2.0")
    uv.available = {"heinzel-acme": "1.2.0"}
    target = tool(tmp_path, AT_VERSION, uv)
    service = UpdateService(target, FakeReleases("0.4.0"), pinned=newer(commit=None))
    agree(service)
    assert uv.commands[-1][-1] == "heinzel-acme>=1.2.0"
    assert _update._AGREED.get().moves == {}
    [pin] = target.plugin_pins()
    assert service._spec(pin, "new", "heinzel", None) == "heinzel-acme==1.0.0"  # nobody agreed: the pin as it is


def test_a_second_update_while_one_installs_is_refused_and_cannot_touch_its_agreement(tmp_path):
    uv = FakeUv("0.2.0")
    uv.available = {"heinzel-acme": "1.2.0"}
    target = tool(tmp_path, AT_VERSION, uv)
    service = UpdateService(target, FakeReleases("0.4.0"), pinned=newer(commit=None))
    plan = service.plan(current="0.2.0")
    installing, proceed = threading.Event(), threading.Event()
    seen: dict[str, object] = {}

    def slow(command, environment=None):
        installing.set()
        assert proceed.wait(10)
        return uv(command, environment)

    target._runner = slow
    first = threading.Thread(target=lambda: seen.update(result=service.update(current="0.2.0", agreed=plan)))
    first.start()
    try:
        assert installing.wait(10)
        # Another request, in its own thread, sees no agreement and may not start a second update.
        assert _update._AGREED.get().moves == {}
        with pytest.raises(HeinzelError, match="An update is already running here"):
            service.update(current="0.2.0")
    finally:
        proceed.set()
        first.join(10)
    assert uv.commands[-1][-1] == "heinzel-acme>=1.2.0"  # the first update installed what it agreed to
    assert seen["result"].plugins[0].after == "1.2.0"  # type: ignore[attr-defined]
    assert service._updating.acquire(blocking=False)  # and the lock is free again
    service._updating.release()


@pytest.mark.parametrize("error", [LookupError("no such release"), KeyError("tag_name"), TypeError("not a list")])
def test_a_plan_answers_in_plain_words_whatever_the_release_sources_raise(tmp_path, error):
    uv = FakeUv("0.2.0")
    target = tool(tmp_path, AT_VERSION, uv)

    def broken(timeout):
        raise error

    plan = UpdateService(target, FakeReleases(error), pinned=broken, newer=broken).plan(current="0.2.0")
    assert plan.core == "hz 0.2.0: the newest release couldn't be asked; the update asks again."
    assert (plan.plugins[0].action, plan.plugins[0].moves_to) == ("kept", None)
    assert "its releases couldn't be asked" in plan.plugins[0].detail


# The remaining words of the plan, and the operations beside the update.


def test_without_a_release_source_for_pins_an_agreed_update_moves_nothing(tmp_path):
    uv = FakeUv("0.2.0")
    target = tool(tmp_path, AT_VERSION, uv)
    result = agree(UpdateService(target, FakeReleases("0.4.0")))
    assert uv.commands[-1][-1] == "heinzel-acme==1.0.0"
    assert result.plugins[0].pinned == "==1.0.0"


def test_the_plan_says_when_hz_is_the_newest_release_or_follows_a_channel(tmp_path):
    uv = FakeUv("0.4.0")
    target = tool(tmp_path, AT_VERSION, uv)
    service = UpdateService(target, FakeReleases("0.4.0"))
    assert service.plan(current="0.4.0").core == "hz 0.4.0 is the newest release."
    assert service.plan(current="0.4.0", channel="edge").core == "hz 0.4.0 follows edge: its newest build is installed."


def test_the_plan_tells_current_pins_followers_and_branches_apart(tmp_path):
    [exact] = planned(tmp_path / "exact", AT_VERSION, PinnedPlugins(current=["heinzel-acme"])).plugins
    assert (exact.action, exact.detail) == ("current", "It follows the package index from now on.")
    [commit] = planned(tmp_path / "commit", AT_COMMIT, PinnedPlugins(current=["heinzel-acme"])).plugins
    assert (commit.action, commit.detail) == ("current", "")
    following = '{ name = "heinzel-acme", specifier = ">=1" }'
    [follower] = planned(tmp_path / "follower", following, PinnedPlugins()).plugins
    assert (follower.action, follower.target, follower.detail) == ("current", None, "")
    branch = f'{{ name = "heinzel-acme", git = "{PLUGINS}?rev=main" }}'
    [moving] = planned(tmp_path / "branch", branch, PinnedPlugins()).plugins
    assert (moving.action, moving.detail) == ("refresh", "Follows its branch; the update asks for its newest commit.")


def test_the_plan_renders_through_the_module_and_marks_what_is_asked_again():
    plan = UpdatePlan(
        current="0.2.0",
        latest=None,
        core="hz 0.2.0.",
        plugins=[PluginPlan(distribution="heinzel-a", installed="1.0.0", action="refresh", detail="Follows main.")],
    )
    assert system.render_plan(plan).plain == "hz 0.2.0.\nPlugins:\n  heinzel-a: asked again (1.0.0). Follows main."


def test_a_preview_from_a_source_says_it_installs_exactly_that():
    service = Service()
    result = run(service, Prompter(), preview=True, source="git+https://example.test/x@v1")
    assert service.calls == []
    assert result.plan is None
    assert result.message == "This was a preview; nothing was changed. `--source` installs exactly what it names."
    assert result.next == ["hz system update --yes --source git+https://example.test/x@v1"]
    assert render_update(result).plain.splitlines()[0] == result.message


class Channels:
    """The channel words of the update service, and the configuration a switch writes."""

    def __init__(self) -> None:
        self.saved: list[tuple[str, str]] = []

    def channel_summary(self, selected: str) -> str:
        return f"Following {selected}."

    def channel_statement(self, selected: str, to: str) -> str:
        return f"From {selected} to {to}."

    def set(self, key: str, value: str) -> None:
        self.saved.append((key, value))


def test_the_channel_is_shown_described_and_switched_only_with_yes():
    channels = Channels()
    shown = system.channel(channels, channels, KERNEL)  # type: ignore[arg-type]
    assert (shown.channel, shown.changed, shown.statement) == ("stable", False, "Following stable.")
    described = system.channel(channels, channels, KERNEL, "edge")  # type: ignore[arg-type]
    assert (described.changed, described.statement, channels.saved) == (False, "From stable to edge.", [])
    switched = system.channel(channels, channels, KERNEL, "edge", yes=True)  # type: ignore[arg-type]
    assert (switched.channel, switched.changed, channels.saved) == ("edge", True, [("update_channel", "edge")])
    assert system.render_channel(switched).plain == "From stable to edge.\nSwitched to edge."
    assert system.render_channel(described).plain == "From stable to edge."


def test_nothing_is_recovered_where_no_automatic_update_stopped_half_way(tmp_path):
    kernel = SimpleNamespace(settings=SimpleNamespace(state_dir=tmp_path))
    assert system.recover_announced(SimpleNamespace(is_tool=True), kernel) is False  # type: ignore[arg-type]
    assert system.recover_announced(SimpleNamespace(is_tool=False), kernel) is False  # type: ignore[arg-type]
