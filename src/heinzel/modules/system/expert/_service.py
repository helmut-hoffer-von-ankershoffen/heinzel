"""Service of the expert module: reads and writes `[hz] expert`."""

from heinzel import Config, ConfigEditor, Kernel, OperationLet, Settings
from heinzel.modules.system.expert._models import ExpertMode, OperationAudience

KEY = "hz.expert"
VARIABLE = "HZ_EXPERT"


class ExpertService:
    """Expert mode: how much of Heinzelmännchen help, completion and the web app list (ADR 0041)."""

    def __init__(self, kernel: Kernel, editor: ConfigEditor) -> None:
        """Create the service; the kernel and the configuration editor are injected."""
        self._kernel = kernel
        self._editor = editor
        # The mode after a change in this process: the kernel read its settings at boot, and `hz ui` keeps running.
        self._expert: bool | None = None

    def mode(self) -> ExpertMode:
        """The mode in effect and every available operation's audience."""
        expert = self._kernel.settings.expert if self._expert is None else self._expert
        return ExpertMode(
            expert=expert,
            operations=[
                OperationAudience(module=record.qualname, operation=let.name, audience=let.audience)
                for record, let in self._kernel.lets(OperationLet)
                if self._kernel.available(let.func)
            ],
            next=["hz system expert off"] if expert else ["hz system expert on"],
        )

    def change(self, *, expert: bool) -> ExpertMode:
        """Write the mode to the user configuration file, then report the mode in effect.

        Raises:
            HeinzelError: If the configuration file can't be read or written.
        """
        self._editor.write(KEY, expert, "user")
        # Read back: HZ_EXPERT wins over every file, so the answer shows the mode in effect, not the one written.
        self._expert = Config.load().settings(Settings, "hz").expert
        result = self.mode()
        if self._expert != expert:
            result.note = (
                f"{VARIABLE} is set in your environment and wins over the configuration file; unset it for "
                f"`hz system expert {'on' if expert else 'off'}` to take effect."
            )
        return result
