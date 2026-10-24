"""The kernel: discovers modules, collects the lets they advertise and injects services.

The kernel knows nothing about any head. Heads (`heinzel.heads`) ask it for lets and decide how to expose them.
"""

from heinzel.kernel._boot import boot
from heinzel.kernel._config import (
    FILENAME,
    KERNEL_SECTION,
    PROJECT_FILENAME,
    Config,
    ConfigFile,
    ModuleSettings,
    env_prefix,
    user_config_dir,
)
from heinzel.kernel._discovery import BUILTIN_PACKAGE, ENTRY_POINT_GROUP, discover, walk
from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._installation import CORE_DISTRIBUTION, Installation, requirement_from_receipt
from heinzel.kernel._kernel import Kernel, ModuleRecord, Origin
from heinzel.kernel._lets import Check, HealthLet, InfoLet, Let, OperationLet, ServiceLet, SettingsLet, Status
from heinzel.kernel._module import Module
from heinzel.kernel._progress import Progress
from heinzel.kernel._release import ReleaseCheck, check_for_update, github_get_json, update_hint
from heinzel.kernel._settings import Settings

__all__ = [
    "BUILTIN_PACKAGE",
    "CORE_DISTRIBUTION",
    "ENTRY_POINT_GROUP",
    "FILENAME",
    "KERNEL_SECTION",
    "PROJECT_FILENAME",
    "Check",
    "Config",
    "ConfigFile",
    "HealthLet",
    "HeinzelError",
    "InfoLet",
    "Installation",
    "Kernel",
    "Let",
    "Module",
    "ModuleRecord",
    "ModuleSettings",
    "OperationLet",
    "Origin",
    "Progress",
    "ReleaseCheck",
    "ServiceLet",
    "Settings",
    "SettingsLet",
    "Status",
    "boot",
    "check_for_update",
    "discover",
    "env_prefix",
    "github_get_json",
    "requirement_from_receipt",
    "update_hint",
    "user_config_dir",
    "walk",
]
