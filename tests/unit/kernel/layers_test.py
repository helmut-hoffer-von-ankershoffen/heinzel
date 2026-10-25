"""Layers: the let a plugin declares, the checked tree, the stack general to specific, recommendations (ADR 0040).

The layers are made up: Acme, its org Payments with the team Checkout, and its site Lisbon.
"""

import pytest

from heinzel import Config, HeinzelError, Kernel, LayerLet, LayerStack, Module, ModuleRecord
from heinzel.kernel import (
    Label,
    LayerConflict,
    LayerProblem,
    LayerProblemKind,
    LayerRecommendation,
    Layers,
    OrgContext,
    Origin,
    _layers,
)
from heinzel.kernel._kernel import ADAPTER_ONLY_LETS
from heinzel.kernel._layers import ORG_SITE_PRECEDENCE, Declared, naming_hint, team_decides

pytestmark = pytest.mark.unit

ACME = "acme"
PAYMENTS = "acme-payments"
CHECKOUT = "acme-payments-checkout"
LISBON = "acme-lisbon"


def declared(
    name: str,
    parent: str | None = None,
    site: str | None = None,
    *,
    distribution: str | None = None,
    plugin: bool = True,
    **defaults: str,
) -> Declared:
    module = Module(name, f"The {name} layer.")
    module.layer(parent=parent, site=site)
    playbook = module.playbook(f"How {name} works.", defaults=defaults, setup=False) if defaults else None
    let = next(let for let in module.lets if isinstance(let, LayerLet))
    return Declared(
        let=let, module=name, distribution=distribution or f"heinzel-{name}", playbook=playbook, plugin=plugin
    )


def acme(**overrides: dict[str, str]) -> Layers:
    """Acme's tree; each layer recommends what `overrides` gives it, as `{layer: {key: value}}`."""
    tree = [(ACME, None, None), (PAYMENTS, ACME, None), (LISBON, ACME, None), (CHECKOUT, PAYMENTS, LISBON)]
    return Layers(
        declared(name, parent, site, **overrides.get(name.replace("-", "_"), {})) for name, parent, site in tree
    )


def kinds(tree: Layers) -> dict[str, set[LayerProblemKind]]:
    found: dict[str, set[LayerProblemKind]] = {}
    for problem in tree.problems:
        found.setdefault(problem.layer, set()).add(problem.problem)
    return found


# The let -------------------------------------------------------------------------------------------------------------


def test_a_layer_names_its_parent_site_and_roles_by_module_name():
    module = Module(CHECKOUT, "Checkout.")
    let = module.layer(parent=PAYMENTS, site=LISBON, roles=["checkout-captain"])
    assert (let.name, let.parent, let.site, let.roles) == (CHECKOUT, PAYMENTS, LISBON, ("checkout-captain",))
    assert let in module.lets


def test_a_module_is_a_layer_once_because_a_layer_has_one_parent():
    module = Module(CHECKOUT, "Checkout.")
    module.layer(parent=PAYMENTS)
    with pytest.raises(ValueError, match="is a layer already; a layer has one parent"):
        module.layer(parent=ACME)


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"parent": CHECKOUT}, "can't be its own parent"),
        ({"site": CHECKOUT}, "can't be its own site"),
        ({"parent": PAYMENTS, "site": PAYMENTS}, "both its parent and its site"),
        ({"parent": "Acme Payments"}, "must be a module name"),
        ({"site": "acme_lisbon"}, "must be a module name"),
        ({"roles": ["Captain"]}, "lowercase words"),
        ({"roles": ["captain", "captain"]}, "twice"),
    ],
)
def test_a_layer_that_refers_to_itself_or_is_malformed_is_refused(arguments, message):
    with pytest.raises(ValueError, match=message):
        Module(CHECKOUT, "Checkout.").layer(**arguments)


def test_a_name_on_the_path_needs_no_hint():
    assert naming_hint(CHECKOUT, PAYMENTS) is None
    assert naming_hint(ACME, None) is None


def test_a_name_off_the_path_gets_a_hint_and_is_declared_all_the_same():
    hint = naming_hint("checkout", PAYMENTS)
    assert hint is not None
    assert "acme-payments-<name>" in hint
    assert "heinzel-acme-payments-<name>" in hint
    assert Module("checkout", "Checkout.").layer(parent=PAYMENTS).parent == PAYMENTS


def test_a_layer_is_pure_data_so_an_adapter_module_stays_adapter_only():
    assert LayerLet in ADAPTER_ONLY_LETS


# The tree ------------------------------------------------------------------------------------------------------------


def test_labels_come_from_position_not_from_a_kind():
    labels = {layer.name: layer.label for layer in acme().layers}
    assert labels == {ACME: Label.COMPANY, PAYMENTS: Label.ORG, LISBON: Label.SITE, CHECKOUT: Label.TEAM}


def test_a_well_formed_tree_has_no_problems_or_hints_and_one_team():
    tree = acme()
    assert (tree.problems, tree.hints) == ([], {})
    assert [layer.name for layer in tree.teams()] == [CHECKOUT]


def test_a_layer_is_found_by_its_name_or_its_plugins_name():
    tree = Layers([declared(ACME, distribution="heinzel-acme-corp")])
    assert tree.get("nope") is None
    assert tree.find("heinzel-acme-corp") is tree.get(ACME)
    assert tree.find(ACME) is tree.get(ACME)


def test_a_missing_parent_or_site_keeps_the_layer_and_names_the_plugin_to_install():
    tree = Layers([declared(CHECKOUT, PAYMENTS, LISBON)])
    problems = {(p.layer, p.problem): p.fix for p in tree.problems}
    assert problems == {
        (CHECKOUT, LayerProblemKind.MISSING_PARENT): "hz system plugin add heinzel-acme-payments",
        (CHECKOUT, LayerProblemKind.MISSING_SITE): "hz system plugin add heinzel-acme-lisbon",
    }
    layer = tree.get(CHECKOUT)
    assert layer is not None
    assert (layer.parent, layer.site, layer.label) == (None, None, Label.TEAM)


def test_a_stack_with_a_missing_link_carries_the_trees_problem():
    stack = Layers([declared(CHECKOUT, PAYMENTS)]).stack(CHECKOUT)
    assert stack.names == (CHECKOUT,)
    assert [p.fix for p in stack.problems] == ["hz system plugin add heinzel-acme-payments"]


def test_a_cycle_leaves_out_every_layer_on_it():
    tree = Layers([declared("a-b", "b-a"), declared("b-a", "a-b"), declared(ACME), declared(PAYMENTS, ACME)])
    cycles = [p for p in tree.problems if p.problem is LayerProblemKind.CYCLE]
    assert {p.layer for p in cycles} == {"a-b", "b-a"}
    assert "a-b → b-a → a-b" in cycles[0].detail
    assert [layer.name for layer in tree.layers] == [ACME, PAYMENTS]


def test_a_layer_two_plugins_declare_would_have_two_parents_and_both_are_left_out():
    tree = Layers([declared(ACME), declared(PAYMENTS, ACME), declared(PAYMENTS, "other", distribution="heinzel-x")])
    (problem,) = tree.problems
    assert problem.problem is LayerProblemKind.DECLARED_TWICE
    assert "parents: acme, other" in problem.detail
    assert problem.fix == (
        "keep one of them: hz system plugin remove heinzel-acme-payments or hz system plugin remove heinzel-x"
    )
    assert tree.get(PAYMENTS) is None


def test_a_layer_declared_twice_with_one_parent_is_declared_twice_all_the_same():
    tree = Layers([declared(ACME), declared(PAYMENTS, ACME), declared(PAYMENTS, ACME, distribution="heinzel-x")])
    assert kinds(tree) == {PAYMENTS: {LayerProblemKind.DECLARED_TWICE}}


def test_the_child_of_a_layer_left_out_points_at_its_parents_problem_not_at_installing_it():
    tree = Layers([declared(ACME), declared(ACME, distribution="heinzel-x"), declared(PAYMENTS, ACME)])
    child = next(p for p in tree.problems if p.layer == PAYMENTS)
    assert child.problem is LayerProblemKind.PARENT_LEFT_OUT
    assert "declared-twice" in child.detail
    assert "plugin add" not in child.fix


@pytest.mark.parametrize("named", [ACME, "heinzel-acme", "heinzel-x"])
def test_your_layer_left_out_says_why_not_that_it_is_missing(named):
    tree = Layers([declared(ACME), declared(ACME, distribution="heinzel-x")])
    (problem,) = tree.stack(named, source="your team").problems
    assert problem.problem is LayerProblemKind.DECLARED_TWICE


def test_your_layer_on_a_cycle_named_by_its_plugin_says_so():
    tree = Layers([declared("acme-a", "acme-b"), declared("acme-b", "acme-a")])
    (problem,) = tree.stack("heinzel-acme-a").problems
    assert problem.problem is LayerProblemKind.CYCLE


def test_the_site_of_a_company_comes_after_it():
    tree = Layers([declared(ACME, site=LISBON), declared(LISBON)])
    assert tree.stack(ACME).names == (ACME, LISBON)


def test_a_submodule_or_built_in_is_not_a_layer():
    tree = Layers([declared(ACME, plugin=False)])
    assert tree.layers == ()
    assert kinds(tree) == {ACME: {LayerProblemKind.NOT_A_PLUGIN}}


def test_a_site_that_is_a_parent_or_below_the_layer_is_ignored_and_reported():
    above = Layers([declared(ACME), declared(PAYMENTS, ACME), declared(CHECKOUT, PAYMENTS, ACME)])
    below = Layers([declared(ACME), declared(PAYMENTS, ACME, CHECKOUT), declared(CHECKOUT, PAYMENTS)])
    for tree, layer in ((above, CHECKOUT), (below, PAYMENTS)):
        assert kinds(tree) == {layer: {LayerProblemKind.SITE_IN_LINE}}
        found = tree.get(layer)
        assert found is not None
        assert found.site is None


def test_a_name_off_the_path_is_hinted():
    tree = Layers([declared(ACME), declared("payments", ACME)])
    assert set(tree.hints) == {"payments"}
    assert not tree.problems


# The stack -----------------------------------------------------------------------------------------------------------


def test_a_teams_stack_is_company_then_org_then_site_then_team():
    stack = acme().stack(CHECKOUT, source="your team")
    assert stack.names == (ACME, PAYMENTS, LISBON, CHECKOUT)
    assert stack.describe() == "acme → acme-payments + acme-lisbon → acme-payments-checkout"
    assert (stack.layer, stack.site, stack.source, stack.problems) == (CHECKOUT, LISBON, "your team", ())
    assert stack.site_ancestors == {ACME}


def test_an_orgs_stack_ends_with_its_site_and_its_teams_inherit_it():
    tree = Layers(
        [declared(ACME), declared(LISBON, ACME), declared(PAYMENTS, ACME, LISBON), declared(CHECKOUT, PAYMENTS)]
    )
    assert tree.stack(PAYMENTS).names == (ACME, PAYMENTS, LISBON)
    assert tree.stack(CHECKOUT).names == (ACME, PAYMENTS, LISBON, CHECKOUT)
    assert tree.stack(ACME).names == (ACME,)


def test_a_site_already_in_the_chain_is_listed_once():
    tree = Layers(
        [declared(ACME), declared(LISBON, ACME), declared(PAYMENTS, ACME, LISBON), declared(CHECKOUT, LISBON)]
    )
    stack = tree.stack(CHECKOUT)
    assert stack.names == (ACME, LISBON, CHECKOUT)
    assert stack.describe() == "acme → acme-lisbon → acme-payments-checkout"


def test_no_layer_is_an_empty_stack():
    assert acme().stack(None) == LayerStack()
    assert LayerStack().describe() == ""


def test_an_unknown_layer_says_which_plugin_to_install():
    (problem,) = acme().stack("acme-refunds", source="your team").problems
    assert problem == LayerProblem(
        layer="acme-refunds",
        problem=LayerProblemKind.NOT_INSTALLED,
        detail="your layer acme-refunds (your team) isn't installed",
        fix="hz system plugin add heinzel-acme-refunds",
    )
    (by_plugin,) = acme().stack("heinzel-acme-refunds").problems
    assert by_plugin.fix == "hz system plugin add heinzel-acme-refunds"


def test_roles_and_playbooks_come_general_to_specific_and_most_specific_first():
    modules = {name: Module(name, name) for name in (ACME, PAYMENTS, CHECKOUT)}
    modules[PAYMENTS].layer(parent=ACME, roles=["payments-lead", "captain"])
    modules[CHECKOUT].layer(parent=PAYMENTS, roles=["captain"])
    modules[ACME].layer()
    playbooks = {name: modules[name].playbook(f"{name}.", setup=False) for name in (ACME, CHECKOUT)}
    tree = Layers(
        Declared(
            let=next(let for let in modules[name].lets if isinstance(let, LayerLet)),
            module=name,
            distribution=f"heinzel-{name}",
            playbook=playbooks.get(name),
        )
        for name in modules
    )
    stack = tree.stack(CHECKOUT)
    assert stack.roles() == ("payments-lead", "captain")
    assert [layer.name for layer, _ in stack.playbooks()] == [CHECKOUT, ACME]


# Recommendations -----------------------------------------------------------------------------------------------------


def test_the_most_specific_layer_is_offered_with_the_general_ones_as_alternatives():
    tree = acme(
        acme={"capabilities.tracker": "acme", "capabilities.calendar": "acme"},
        acme_payments={"capabilities.tracker": "acme-payments"},
        acme_payments_checkout={"capabilities.tracker": "checkout"},
    )
    offered, conflicts = tree.stack(CHECKOUT).recommendations()
    assert conflicts == ()
    assert offered == (
        LayerRecommendation(
            key="capabilities.tracker",
            value="checkout",
            layer=CHECKOUT,
            alternatives=(("acme-payments", "acme-payments"), ("acme", "acme")),
        ),
        LayerRecommendation(key="capabilities.calendar", value="acme", layer=ACME),
    )


def test_an_org_and_the_site_disagreeing_is_a_conflict_when_the_team_is_silent():
    tree = acme(
        acme={"capabilities.calendar": "acme"},
        acme_payments={"capabilities.calendar": "payments"},
        acme_lisbon={"capabilities.calendar": "lisbon"},
    )
    offered, conflicts = tree.stack(CHECKOUT).recommendations()
    assert offered == ()
    assert conflicts == (
        LayerConflict(
            key="capabilities.calendar",
            values=(("acme", "acme"), ("acme-payments", "payments"), ("acme-lisbon", "lisbon")),
        ),
    )


def test_the_team_decides_an_org_site_conflict():
    tree = acme(
        acme_payments={"capabilities.calendar": "payments"},
        acme_lisbon={"capabilities.calendar": "lisbon"},
        acme_payments_checkout={"capabilities.calendar": "payments"},
    )
    (offered,), conflicts = tree.stack(CHECKOUT).recommendations()
    assert conflicts == ()
    assert (offered.value, offered.layer, offered.alternatives) == ("payments", CHECKOUT, (("acme-lisbon", "lisbon"),))


def test_the_site_refines_the_company_and_agreeing_layers_are_no_conflict():
    tree = acme(
        acme={"capabilities.calendar": "acme", "capabilities.chat": "acme"},
        acme_payments={"capabilities.chat": "acme"},
        acme_lisbon={"capabilities.calendar": "lisbon", "capabilities.chat": "acme"},
    )
    offered, conflicts = tree.stack(CHECKOUT).recommendations()
    assert conflicts == ()
    assert {(r.key, r.value, r.layer) for r in offered} == {
        ("capabilities.calendar", "lisbon", LISBON),
        ("capabilities.chat", "acme", LISBON),
    }


def test_an_orgs_own_stack_lets_the_org_decide_against_its_site():
    tree = Layers(
        [
            declared(ACME),
            declared(LISBON, ACME, **{"capabilities.calendar": "lisbon"}),
            declared(PAYMENTS, ACME, LISBON, **{"capabilities.calendar": "payments"}),
        ]
    )
    (offered,), _ = tree.stack(PAYMENTS).recommendations()
    assert (offered.layer, offered.value) == (PAYMENTS, "payments")


def test_a_stack_without_a_site_never_conflicts():
    tree = Layers(
        [declared(ACME, **{"capabilities.chat": "a"}), declared(PAYMENTS, ACME, **{"capabilities.chat": "b"})]
    )
    (offered,), conflicts = tree.stack(PAYMENTS).recommendations()
    assert (offered.value, conflicts) == ("b", ())


def test_the_org_site_rule_is_one_assignment(monkeypatch):
    assert ORG_SITE_PRECEDENCE is team_decides

    def general_wins(stack, key, values):
        layer, value = values[0]
        return LayerRecommendation(key=key, value=value, layer=layer.name)

    monkeypatch.setattr(_layers, "ORG_SITE_PRECEDENCE", general_wins)
    tree = Layers(
        [declared(ACME, **{"capabilities.chat": "a"}), declared(PAYMENTS, ACME, **{"capabilities.chat": "b"})]
    )
    (offered,), _ = tree.stack(PAYMENTS).recommendations()
    assert (offered.layer, offered.value) == (ACME, "a")


def test_a_key_only_the_site_recommends_is_offered_from_the_site():
    tree = acme(acme_lisbon={"acme-lisbon.zone": "Europe/Lisbon"}, acme_payments={"capabilities.chat": "payments"})
    offered, conflicts = tree.stack(CHECKOUT).recommendations()
    assert conflicts == ()
    assert [(r.key, r.layer) for r in offered] == [("acme-lisbon.zone", LISBON), ("capabilities.chat", PAYMENTS)]


# The kernel ----------------------------------------------------------------------------------------------------------


def layer_module(name: str, parent: str | None = None, site: str | None = None) -> Module:
    module = Module(name, f"The {name} layer.")
    module.layer(parent=parent, site=site)
    return module


class Declares(OrgContext):
    def __init__(self, layer: str | None = None, error: Exception | None = None) -> None:
        self._layer, self._error = layer, error

    def team_checkout(self):
        return None

    def layer(self):
        if self._error is not None:
            raise self._error
        return self._layer


def with_context(context: OrgContext) -> Module:
    module = Module("context", "Provides the org context.")
    module.service(OrgContext, lambda: context)
    return module


def acme_kernel(make_kernel, *extra: Module, **more) -> Kernel:
    layers = {
        ACME: layer_module(ACME),
        PAYMENTS: layer_module(PAYMENTS, ACME),
        LISBON: layer_module(LISBON, ACME),
        CHECKOUT: layer_module(CHECKOUT, PAYMENTS, LISBON),
    }
    return make_kernel(**layers, **{m.name: m for m in extra}, **more)


def board() -> object:
    return object()


def test_a_layer_brings_the_adapters_the_kernel_registered_for_its_plugin_and_its_playbook(make_kernel):
    company = layer_module(ACME)
    company.provides("tracker", modes={"read"})(board)
    company.provides("calendar", modes={"read"})(object)  # lacks the methods: skipped, so not brought
    company.playbook("How Acme works.", setup=False)
    kernel = make_kernel(acme=company, acme__sub=Module("sub", "A submodule."))
    (layer,) = kernel.layers().layers
    assert (layer.module, layer.distribution, layer.adapters) == ("acme", "heinzel-test", ("acme:tracker",))
    assert layer.playbook is not None


def test_a_submodules_layer_is_reported_not_stacked(make_kernel):
    kernel = make_kernel(acme=layer_module(ACME), acme__ops=layer_module("ops", ACME))
    assert [layer.name for layer in kernel.layers().layers] == [ACME]
    assert [p.problem for p in kernel.layers().problems] == [LayerProblemKind.NOT_A_PLUGIN]


def test_without_a_declaration_the_only_team_layer_is_yours(make_kernel):
    stack = acme_kernel(make_kernel).layer_stack()
    assert stack.names == (ACME, PAYMENTS, LISBON, CHECKOUT)
    assert stack.source == "the only team layer installed"


def test_another_layers_stack_is_built_on_request(make_kernel):
    stack = acme_kernel(make_kernel).layer_stack(PAYMENTS)
    assert (stack.names, stack.source) == ((ACME, PAYMENTS), "asked for")


def test_what_the_team_declares_beats_what_is_installed(make_kernel):
    stack = acme_kernel(make_kernel, with_context(Declares(PAYMENTS))).layer_stack()
    assert (stack.layer, stack.source) == (PAYMENTS, "your team")


def test_the_team_may_name_its_layer_by_plugin(settings):
    records = [
        ModuleRecord(
            qualname=name, module=module, origin=Origin(distribution=f"heinzel-{name}", version="1", builtin=False)
        )
        for name, module in ((ACME, layer_module(ACME)), (PAYMENTS, layer_module(PAYMENTS, ACME)))
    ]
    context = with_context(Declares("heinzel-acme-payments"))
    records.append(ModuleRecord(qualname="context", module=context, origin=records[0].origin))
    assert Kernel(settings, records).layer_stack().layer == PAYMENTS


def test_a_declared_layer_that_isnt_installed_is_no_stack_with_the_plugin_to_add(make_kernel):
    stack = acme_kernel(make_kernel, with_context(Declares("acme-refunds"))).layer_stack()
    assert stack.layers == ()
    assert [p.fix for p in stack.problems] == ["hz system plugin add heinzel-acme-refunds"]


def test_an_unreadable_team_leaves_the_stack_to_what_is_installed_and_says_why(make_kernel):
    context = with_context(Declares(error=HeinzelError("hz.yaml is missing")))
    stack = acme_kernel(make_kernel, context).layer_stack()
    assert stack.layer == CHECKOUT
    assert [(p.problem, p.detail) for p in stack.problems] == [
        (LayerProblemKind.UNREADABLE, "your team's layer couldn't be read: hz.yaml is missing")
    ]


def test_an_invalid_team_layer_setting_leaves_the_stack_to_what_is_installed(make_kernel):
    config = Config(files=(), data={"team": {"layer": "Bad Name"}})
    stack = acme_kernel(make_kernel, builtins=True, config=config).layer_stack()
    assert stack.layer == CHECKOUT
    (problem,) = stack.problems
    assert problem.detail.startswith("your team's layer couldn't be read: Value error, 'Bad Name' isn't a layer name")
    assert "\n" not in problem.detail


def test_two_team_layers_and_no_declaration_is_no_stack_with_the_command_that_picks(make_kernel):
    kernel = acme_kernel(make_kernel, **{"acme-payments-refunds": layer_module("acme-payments-refunds", PAYMENTS)})
    stack = kernel.layer_stack()
    assert stack.layers == ()
    assert stack.problems == (
        LayerProblem(
            layer="",
            problem=LayerProblemKind.AMBIGUOUS,
            detail="2 team layers are installed (acme-payments-checkout, acme-payments-refunds) and nothing says "
            "which is yours",
            fix="hz system config set team.layer acme-payments-checkout",
        ),
    )


def test_two_companies_and_no_team_say_companies(make_kernel):
    (problem,) = make_kernel(acme=layer_module(ACME), **{"acme-labs": layer_module("acme-labs")}).layer_stack().problems
    assert problem.detail.startswith("2 company layers are installed")


def test_a_lone_company_is_your_stack_and_no_layer_is_none(make_kernel):
    assert make_kernel(acme=layer_module(ACME)).layer_stack().names == (ACME,)
    assert make_kernel().layer_stack() == LayerStack()


def test_the_stack_is_built_afresh_so_a_change_in_this_process_counts(make_kernel):
    declares = Declares(PAYMENTS)
    kernel = acme_kernel(make_kernel, with_context(declares))
    assert kernel.layer_stack().layer == PAYMENTS
    declares._layer = CHECKOUT
    assert kernel.layer_stack().layer == CHECKOUT


def test_the_base_org_context_declares_no_layer():
    assert OrgContext().layer() is None


def test_layers_of_disabled_modules_are_missing(settings):
    origin = Origin(distribution="heinzel-acme", version="1", builtin=False)
    records = [
        ModuleRecord(qualname=ACME, module=layer_module(ACME), origin=origin),
        ModuleRecord(qualname=PAYMENTS, module=layer_module(PAYMENTS, ACME), origin=origin),
    ]
    kernel = Kernel(settings.model_copy(update={"disabled_modules": [ACME]}), records)
    assert [p.problem for p in kernel.layers().problems] == [LayerProblemKind.MISSING_PARENT]
