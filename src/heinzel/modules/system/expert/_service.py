"""Service of the expert module: reads and writes `[hz] expert`."""

import os

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

    def mode(self) -> ExpertMode:
        """The mode in effect, why when the user file doesn't decide it, and every available operation's audience.

        Raises:
            HeinzelError: If a configuration file is not valid TOML.
        """
        # Read now, not from the settings of boot: `hz ui` keeps running while a terminal switches the mode.
        config = Config.load()
        expert = config.settings(Settings, "hz").expert
        note, next_steps = None, ["hz system expert off" if expert else "hz system expert on"]
        if variable := next((name for name in os.environ if name.upper() == VARIABLE), None):
            note = f"{variable}={os.environ[variable]} in your environment decides the mode; unset it to switch."
            next_steps = [f"unset {variable}"]
        elif explicit := next(
            (
                file
                for file, content in config.layers
                if file.scope == "explicit" and "expert" in Config(files=(), data=content).section("hz")
            ),
            None,
        ):
            note = f"{explicit.path} (HZ_CONFIG) sets hz.expert and wins over your user file; change it there."
            next_steps = []
        return ExpertMode(
            expert=expert,
            operations=[
                OperationAudience(module=record.qualname, operation=let.name, audience=let.audience)
                for record, let in self._kernel.lets(OperationLet)
                if self._kernel.available(let.func)
            ],
            note=note,
            next=next_steps,
        )

    def change(self, *, expert: bool) -> ExpertMode:
        """Write the mode to the user configuration file, then report the mode in effect.

        Raises:
            HeinzelError: If the configuration file can't be read or written.
        """
        self._editor.write(KEY, expert, "user")
        return self.mode()
