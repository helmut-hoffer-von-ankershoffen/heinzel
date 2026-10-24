"""Service of the layer module: the installed layers, the stack you are in and their health (ADR 0040)."""

import re
import shlex
from typing import Any, cast

from pydantic import JsonValue
from pydantic_core import to_jsonable_python

from heinzel import Check, Kernel, Status
from heinzel.kernel import Layer, LayerConflict, LayerProblem, LayerRecommendation, LayerStack, toml_value
from heinzel.modules.system.layer._models import (
    ConflictView,
    LayerList,
    LayerProblemView,
    LayerView,
    RecommendationView,
    StackView,
)

_WORD = re.compile(r"[A-Za-z0-9_./:@-]+")


def set_command(key: str, value: object) -> str:
    """The `hz system config set` line that applies `value` to `key`, quoted for a shell."""
    text = value if isinstance(value, str) and _WORD.fullmatch(value) else shlex.quote(toml_value(value))
    return f"hz system config set {key} {text}"


def _json(value: Any) -> JsonValue:  # noqa: ANN401 - recommended values are free-form TOML values
    return cast("JsonValue", to_jsonable_python(value))


class LayerService:
    """Reports layers and stacks from the kernel's checked tree; never changes configuration."""

    def __init__(self, kernel: Kernel) -> None:
        """Work on the kernel's layers, injected."""
        self._kernel = kernel

    def listing(self) -> LayerList:
        """Every installed layer and what is wrong with the tree."""
        tree = self._kernel.layers()
        problems = [_problem(p) for p in tree.problems]
        if tree.layers:
            steps = ["hz system layer show"]
        elif problems:
            steps = [problems[0].fix]
        else:
            steps = ["hz system plugin add heinzel-<your-team>"]
        return LayerList(layers=[self._view(layer, tree.hints) for layer in tree.layers], problems=problems, next=steps)

    def show(self, layer: str | None = None) -> StackView:
        """The stack of `layer`, or of the layer you are in, and what its layers bring together."""
        stack = self._kernel.layer_stack(layer)
        hints = self._kernel.layers().hints
        recommendations, conflicts = stack.recommendations()
        views = [self._recommendation(r) for r in recommendations]
        steps = [view.command for view in views]
        if not stack.layers and not stack.problems:
            steps.append("hz system layer list")
        return StackView(
            layer=stack.layer,
            source=stack.source,
            stack=stack.describe(),
            layers=[self._view(found, hints) for found in stack.layers],
            roles=list(stack.roles()),
            recommendations=views,
            conflicts=[ConflictView(key=c.key, values=dict(_values(c)), fix=_decide(stack, c)) for c in conflicts],
            problems=[_problem(p) for p in stack.problems],
            next=steps,
        )

    def info(self) -> dict[str, str]:
        """Your stack as a section of `hz system info`; empty when no layer is installed."""
        tree = self._kernel.layers()
        if not tree.layers and not tree.problems:
            return {}
        stack = self._kernel.layer_stack()
        section = {"stack": stack.describe() or "none", "layer": stack.layer or "none", "from": stack.source or "-"}
        if roles := stack.roles():
            section["roles"] = ", ".join(roles)
        if stack.problems:
            section["problems"] = "; ".join(f"{p.detail} ({p.fix})" for p in stack.problems)
        return section

    def checks(self) -> list[Check]:
        """The tree's problems, your stack's and its undecided conflicts, degraded; naming hints, up."""
        tree = self._kernel.layers()
        if not tree.layers and not tree.problems:
            return []
        checks = [
            Check(name=f"{p.layer} {p.problem.value}", status=Status.DEGRADED, detail=p.detail, fix=p.fix)
            for p in tree.problems
        ]
        stack = self._kernel.layer_stack()
        checks += [
            Check(name=f"stack {p.problem.value}", status=Status.DEGRADED, detail=p.detail, fix=p.fix)
            for p in stack.problems
            if p not in tree.problems  # the tree's own are listed above
        ]
        _, conflicts = stack.recommendations()
        checks += [
            Check(name=f"conflict {c.key}", status=Status.DEGRADED, detail=_disagree(c), fix=_decide(stack, c))
            for c in conflicts
        ]
        if not checks:
            checks.append(Check(name="stack", status=Status.UP, detail=stack.describe() or "no layer is yours"))
        checks += [
            Check(name=f"{name} naming", status=Status.UP, detail=hint, fix="rename the plugin when it next moves")
            for name, hint in tree.hints.items()
        ]
        return checks

    @staticmethod
    def _view(layer: Layer, hints: dict[str, str]) -> LayerView:
        return LayerView(
            name=layer.name,
            label=layer.label.value,
            parent=layer.parent,
            site=layer.site,
            plugin=layer.distribution,
            module=layer.module,
            adapters=list(layer.adapters),
            roles=list(layer.roles),
            playbook=layer.playbook.summary if layer.playbook else None,
            hint=hints.get(layer.name),
        )

    @staticmethod
    def _recommendation(recommendation: LayerRecommendation) -> RecommendationView:
        return RecommendationView(
            key=recommendation.key,
            value=_json(recommendation.value),
            layer=recommendation.layer,
            alternatives={layer: _json(value) for layer, value in recommendation.alternatives},
            command=set_command(recommendation.key, recommendation.value),
        )


def _problem(problem: LayerProblem) -> LayerProblemView:
    return LayerProblemView(layer=problem.layer, problem=problem.problem.value, detail=problem.detail, fix=problem.fix)


def _values(conflict: LayerConflict) -> list[tuple[str, JsonValue]]:
    return [(layer, _json(value)) for layer, value in conflict.values]


def _disagree(conflict: LayerConflict) -> str:
    said = ", ".join(f"{layer} recommends {toml_value(value)}" for layer, value in conflict.values)
    return f"{said} for {conflict.key}; hz offers neither"


def _decide(stack: LayerStack, conflict: LayerConflict) -> str:
    team = stack.layer or "your team's layer"
    _, value = conflict.values[-1]
    return (
        f"{team} decides by recommending {conflict.key} in its playbook; until then your file decides, "
        f"e.g. {set_command(conflict.key, value)}"
    )
