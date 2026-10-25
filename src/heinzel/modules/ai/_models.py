"""What the chat's model and effort picker shows: the models and efforts of the AI access in use (ADR 0057)."""

from pydantic import BaseModel, Field

from heinzel import AccessStatus, Adapters, AiAccess
from heinzel.kernel import Billing, CapabilityView, Effort

EFFORT_LABELS: dict[Effort, str] = {"low": "Low", "medium": "Medium", "high": "High", "xhigh": "Extra", "max": "Max"}
RECOMMENDED: Effort = "medium"
WARNINGS: dict[Effort, str] = {"max": "≈3.5x or more usage"}


class ModelChoice(BaseModel):
    """A model the AI access in use offers."""

    name: str = Field(description="What `hz chat send --model` takes, e.g. `sonnet`.")
    title: str = Field(description="What the picker shows, e.g. `Sonnet`.")


class EffortChoice(BaseModel):
    """An effort the AI access in use can be asked for."""

    name: Effort = Field(description="What `hz chat send --effort` takes, e.g. `medium`.")
    title: str = Field(description="What the picker shows, e.g. `Extra`.")
    recommended: bool = Field(description="Whether it is the one to pick when unsure.")
    warning: str | None = Field(
        default=None, description="What it costs beyond the others, e.g. `≈3.5x or more usage`."
    )


class ModelList(BaseModel):
    """The models and efforts of the AI access in use, for the chat's picker."""

    access: str | None = Field(description="The AI access in use, e.g. `claude-code`; none when none is.")
    title: str = Field(description="Its title, e.g. `Claude Code`.")
    billing: Billing | None = Field(description="`plan` when it uses a subscription, `api` when billed per token.")
    models: list[ModelChoice] = Field(description="Its models, current ones first; empty when it names none.")
    efforts: list[EffortChoice] = Field(description="The efforts it takes, low to max; empty when it takes none.")
    note: str | None = Field(default=None, description="Why the list is empty or short.")


def models(options: Adapters[AiAccess], view: CapabilityView) -> ModelList:
    """The picker's models and efforts, from the status of the AI access in use; calls no model."""
    selected = view.bound("ai-access")
    if selected is None or selected not in options:
        note = "No AI access is in use, so there are no models to pick." if selected is None else None
        note = note or f"No installed AI access option is called {selected!r}."
        return ModelList(access=selected, title=selected or "", billing=None, models=[], efforts=[], note=note)
    try:
        status: AccessStatus = options[selected].status()
    except Exception as exc:  # noqa: BLE001 - a plugin's option never breaks the picker
        return ModelList(
            access=selected, title=selected, billing=None, models=[], efforts=[], note=f"{type(exc).__name__}"
        )
    return ModelList(
        access=selected,
        title=status.title,
        billing=status.billing,
        models=[ModelChoice(name=m, title=m.replace("-", " ").title()) for m in status.models],
        efforts=[
            EffortChoice(
                name=effort,
                title=EFFORT_LABELS[effort],
                recommended=effort == RECOMMENDED,
                warning=WARNINGS.get(effort),
            )
            for effort in status.efforts
        ],
        note=None if status.models else f"{status.title} names no models; it uses its own default.",
    )
