"""The agent module: Heinzelmännchen's Agent Skills and their installation for coding agents."""

from heinzel import Module
from heinzel.modules.system.agent._models import AgentSettings, InstalledSkill, Skill
from heinzel.modules.system.agent._service import AgentService

module = Module("agent", "Install the Heinzelmännchen skills for coding agents.")
module.settings(AgentSettings)
module.service(AgentService)


@module.operation
def skills(service: AgentService) -> list[Skill]:
    """List the Agent Skills that ship with Heinzelmännchen."""
    return service.skills()


@module.operation(read_only=False)
def install(service: AgentService) -> list[InstalledSkill]:
    """Copy Heinzelmännchen's skills into each configured agent skill directory."""
    return service.install()


@module.operation(read_only=False)
def uninstall(service: AgentService) -> list[InstalledSkill]:
    """Remove Heinzelmännchen's skills from each configured agent skill directory."""
    return service.uninstall()


__all__ = ["AgentService", "AgentSettings", "InstalledSkill", "Skill", "module"]
