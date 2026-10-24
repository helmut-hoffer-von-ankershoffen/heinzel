"""The layer module: which layer plugins are installed, and the stack you are in, general to specific (ADR 0040).

A layer is a plugin that declares `module.layer(parent=..., site=...)`. The kernel checks the tree and stacks the
layers you are in: your company, each org down to your team, your site, your team. This module shows the tree and the
stack, with what each layer brings (adapters, roles, a playbook) and the settings the stack recommends, most specific
first; it applies none of them.
"""

from typing import Annotated

from pydantic import Field

from heinzel import Check, Module
from heinzel.modules.system.layer._models import (
    ConflictView,
    LayerList,
    LayerProblemView,
    LayerView,
    RecommendationView,
    StackView,
)
from heinzel.modules.system.layer._render import render_list, render_stack
from heinzel.modules.system.layer._service import LayerService, set_command

module = Module("layer", "Show the layer plugins installed and the stack you are in, general to specific.")
module.service(LayerService)


@module.operation(name="list", render=render_list)
def list_(service: LayerService) -> LayerList:
    """List the installed layer plugins: where each sits, what it brings and what is wrong with the tree."""
    return service.listing()


@module.operation(render=render_stack)
def show(
    service: LayerService,
    layer: Annotated[
        str | None, Field(description="Show this layer's stack instead of yours, e.g. acme-payments-checkout.")
    ] = None,
) -> StackView:
    """Show your stack: company, org and site, team; what the layers bring and the settings they recommend."""
    return service.show(layer)


@module.info
def stack(service: LayerService) -> dict[str, str]:
    """Your stack as a section of `hz system info`."""
    return service.info()


@module.health
def layers(service: LayerService) -> list[Check]:
    """Every layer's parent and site is installed, the tree has no cycle, and an org and the site agree."""
    return service.checks()


__all__ = [
    "ConflictView",
    "LayerList",
    "LayerProblemView",
    "LayerService",
    "LayerView",
    "RecommendationView",
    "StackView",
    "module",
    "set_command",
]
