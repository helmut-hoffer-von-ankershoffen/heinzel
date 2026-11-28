"""Original agent model entry used by 0.9.52 and 0.9.53 during wheel replacement."""

import sys

_current = sys.modules.get("heinzel.kernel.ai._agents")
# A process already running 0.9.54 or later owns these objects at the newer path.
# In a fresh process that facade is still initializing and has not exported them.
if _current is not None and "AgentResult" in vars(_current):
    from heinzel.kernel.ai._agents import (
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
else:
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
