"""What a chat may never do, even with the person's confirmation: install code, or widen what agents may do.

A chat makes confirming easy, so a prompt-injected agent's plausible proposal is one click away. Operations that
install or run code of someone else's choosing, or that change what runs, what agents may use and what a chat may
spend, are refused when the agent proposes them and again at confirm; the person runs them in a terminal, where they
type what they mean (ADR 0045).
"""

from pydantic import JsonValue

NOT_FROM_A_CHAT = frozenset(
    {
        ("system", "update"),
        ("system.agent", "install"),
        ("system.agent", "uninstall"),
        ("system.config", "init"),
        ("system.credential", "remove"),
        ("system.credential", "set"),
        ("system.daemon", "install"),
        ("system.daemon", "uninstall"),
        ("system.plugin", "add"),
        ("system.plugin", "new"),
        ("system.plugin", "remove"),
    }
)
"""Operations that install code, store secrets or schedule runs."""
GUARDED_SETTINGS = ("hz.", "capabilities.", "system.runner.", "system.credential.", "system.daemon.")
"""Settings `system config set/unset` may not change from a chat: what runs, with which tools, bindings and caps."""


def refusal(module: str, operation: str, arguments: dict[str, JsonValue]) -> str | None:
    """Why a chat may not run this operation, or None when it may (with the person's confirmation)."""
    command = f"hz {module.replace('.', ' ')} {operation}"
    if (module, operation) in NOT_FROM_A_CHAT:
        return f"`{command}` can't be run from a chat; the person can run it in a terminal."
    key = arguments.get("key")
    if module == "system.config" and operation in {"set", "unset"} and isinstance(key, str):
        normalized = key.strip().lower().replace("-", "_")
        if normalized.startswith(GUARDED_SETTINGS):
            return (
                f"The setting {key} decides what runs, with which tools or at what cost, so a chat can't change it; "
                f"the person can run `{command} {key} ...` in a terminal."
            )
    return None
