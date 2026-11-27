"""Compatibility path for the agent models held by processes that update in place."""

from types import ModuleType
from typing import TYPE_CHECKING

from heinzel.kernel import _agents as _resident

if TYPE_CHECKING:
    from heinzel.kernel._agent_models import (
        AgentResult,
        AgentRunner,
        Billing,
        Budget,
        ChatToolSet,
        Effort,
        HzTools,
        McpServer,
        RunStatus,
        TokenUsage,
        ToolProfile,
    )


def _source_models() -> ModuleType:
    """Reuse the resident generation when it contains the complete chat-tool contract."""
    if hasattr(_resident, "ChatToolSet") and hasattr(_resident, "HzTools"):
        return _resident
    # 0.9.14 and 0.9.15 still need the newer model contract as a complete generation.
    from heinzel.kernel import _agent_models  # noqa: PLC0415 - only this older generation needs fresh definitions

    return _agent_models


if not TYPE_CHECKING:
    _models = _source_models()
    AgentResult = _models.AgentResult
    AgentRunner = _models.AgentRunner
    Billing = _models.Billing
    Budget = _models.Budget
    ChatToolSet = _models.ChatToolSet
    Effort = _models.Effort
    HzTools = _models.HzTools
    McpServer = _models.McpServer
    RunStatus = _models.RunStatus
    TokenUsage = _models.TokenUsage
    ToolProfile = _models.ToolProfile

__all__ = [
    "AgentResult",
    "AgentRunner",
    "Billing",
    "Budget",
    "ChatToolSet",
    "Effort",
    "HzTools",
    "McpServer",
    "RunStatus",
    "TokenUsage",
    "ToolProfile",
]
