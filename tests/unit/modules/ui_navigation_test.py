"""UI projection comes from active provider declarations and actual owned Settings lets."""

import pytest

from heinzel import Module, ModuleRecord, ModuleSettings, Navigate, NavSettings, RunOperation, Settings
from heinzel.kernel import Origin
from heinzel.modules.system.ui import UiService

pytestmark = pytest.mark.unit


class HarborSettings(ModuleSettings):
    section_title = "Harbor preferences"
    limit: int = 3


def provider(*, explicit=True):
    module = Module("harbor", "Harbor tools.")
    module.settings(HarborSettings)
    if explicit:
        module.nav(
            title="Harbor",
            icon="cloud-sun",
            order=11,
            settings=NavSettings(sections=("harbor",), group="customize"),
            sidebar=Navigate(path="/?settings=harbor"),
        )
    return module


def harbor_entries(kernel):
    return [entry for entry in kernel.resolve(UiService).navigation() if entry.module.startswith("harbor")]


def test_arbitrary_provider_supplies_semantics_and_replaces_legacy_fallback(make_kernel):
    (entry,) = harbor_entries(make_kernel(builtins=True, harbor=provider()))
    assert (entry.id, entry.title, entry.icon, entry.order) == ("harbor:main", "Harbor", "cloud-sun", 11)
    assert (entry.plugin, entry.settings.sections, entry.settings.group) == ("heinzel-test", ("harbor",), "customize")
    assert entry.sidebar.path == "/?settings=harbor"
    assert entry.problem is None
    assert entry.declared is True


def test_legacy_settings_keep_a_generic_entry_without_any_sidebar_guess(make_kernel):
    (entry,) = harbor_entries(make_kernel(builtins=True, harbor=provider(explicit=False)))
    assert (entry.id, entry.title, entry.icon) == ("harbor:@settings", "Harbor preferences", "code")
    assert entry.sidebar is None
    assert entry.declared is False
    assert entry.settings.sections == ("harbor",)


def test_disabled_and_uninstalled_providers_have_no_navigation(make_kernel):
    assert harbor_entries(make_kernel(builtins=True)) == []
    assert (
        harbor_entries(make_kernel(builtins=True, harbor=provider(), settings_=Settings(disabled_modules={"harbor"})))
        == []
    )


def test_owned_descendant_prefixes_replace_descendant_legacy_fallback(make_kernel):
    parent = Module("harbor", "Harbor tools.")
    child = Module("alerts", "Alert tools.")
    child.settings(HarborSettings)
    parent.nav(title="Harbor", icon="bell", settings=NavSettings(sections=("harbor",)))
    entries = harbor_entries(make_kernel(builtins=True, harbor=parent, harbor__alerts=child))
    assert [entry.id for entry in entries] == ["harbor:main"]


@pytest.mark.parametrize("prefix", ["elsewhere", "hz", "harbor.missing"])
def test_foreign_kernel_and_missing_sections_have_no_executable_target(make_kernel, prefix):
    module = provider(explicit=False)
    module.nav(title="Broken", icon="code", settings=NavSettings(sections=(prefix,)), sidebar=Navigate(path="/"))
    kernel = make_kernel(builtins=True, harbor=module)
    entries = harbor_entries(kernel)
    broken = next(entry for entry in entries if entry.id == "harbor:main")
    assert broken.problem is not None
    assert broken.settings is broken.sidebar is None
    assert (broken.id, broken.problem) in kernel.resolve(UiService).problems()
    assert any(entry.id == "harbor:@settings" for entry in entries)


def test_navigation_builds_its_own_operation_call_without_running_it(make_kernel):
    module = Module("harbor", "Harbor tools.")
    calls = []

    @module.operation(read_only=False)
    def change(limit: int) -> int:
        """Change the limit."""
        calls.append(limit)
        return limit

    module.nav(title="Harbor", icon="code", sidebar=RunOperation(operation="change", arguments={"limit": 4}))
    (entry,) = harbor_entries(make_kernel(builtins=True, harbor=module))
    assert calls == []
    assert entry.sidebar.call.path == "/harbor/change"
    assert entry.sidebar.call.query == {"limit": 4}
    assert entry.sidebar.call.body is None
    assert entry.sidebar.call.read_only is False


@pytest.mark.parametrize("owns_parent", [False, True])
def test_a_parent_cannot_claim_a_descendant_from_another_distribution(make_kernel, owns_parent):
    parent = Module("harbor", "Harbor tools.")
    if owns_parent:
        parent.settings(HarborSettings)
    prefix = "harbor" if owns_parent else "harbor.extra"
    parent.nav(title="Harbor", icon="code", settings=NavSettings(sections=(prefix,)))
    kernel = make_kernel(builtins=True, harbor=parent)
    child = Module("extra", "Another provider.")
    child.settings(HarborSettings)
    records = [
        *kernel.modules,
        ModuleRecord(
            qualname="harbor.extra",
            module=child,
            origin=Origin(distribution="heinzel-other", version="1.0", builtin=False),
        ),
    ]
    from heinzel import Kernel

    combined = Kernel(records=records)
    assert any(record.qualname == "harbor.extra" for record in combined.modules)
    entries = harbor_entries(combined)
    parent_entry = next(entry for entry in entries if entry.id == "harbor:main")
    assert parent_entry.settings is None
    assert parent_entry.problem is not None


def test_private_operation_arguments_must_match_the_strict_json_body_type(make_kernel):
    module = Module("harbor", "Harbor tools.")

    @module.operation(private=True, read_only=False)
    def change(limit: int) -> int:
        """Change the limit."""
        return limit

    module.nav(title="Harbor", icon="code", sidebar=RunOperation(operation="change", arguments={"limit": "4"}))
    (entry,) = harbor_entries(make_kernel(builtins=True, harbor=module))
    assert entry.sidebar is None
    assert "limit" in entry.problem


@pytest.mark.parametrize("invalid_settings", [False, True])
def test_explicit_settings_name_cannot_collide_with_reserved_legacy_settings_identity(make_kernel, invalid_settings):
    module = provider(explicit=False)
    module.nav(
        "settings",
        title="Harbor",
        icon="code",
        sidebar=Navigate(path="/"),
        settings=NavSettings(sections=("elsewhere",)) if invalid_settings else None,
    )
    entries = harbor_entries(make_kernel(builtins=True, harbor=module))
    assert {entry.id for entry in entries} == {"harbor:settings", "harbor:@settings"}
    assert len(entries) == 2
    explicit = next(entry for entry in entries if entry.declared)
    fallback = next(entry for entry in entries if not entry.declared)
    assert fallback.settings.sections == ("harbor",)
    assert (explicit.sidebar is None) is invalid_settings
