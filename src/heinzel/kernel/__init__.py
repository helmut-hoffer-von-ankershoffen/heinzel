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
from heinzel.kernel._credentials import (
    CredentialBackend,
    Credentials,
    DeclaredCredential,
    EnvironmentBackend,
    environment_variable,
)
from heinzel.kernel._discovery import BUILTIN_PACKAGE, ENTRY_POINT_GROUP, discover, walk
from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._installation import CORE_DISTRIBUTION, Installation, requirement_from_receipt
from heinzel.kernel._kernel import Kernel, ModuleRecord, Origin
from heinzel.kernel._lets import (
    Check,
    CredentialLet,
    HealthLet,
    InfoLet,
    Let,
    OperationLet,
    ServiceLet,
    SettingsLet,
    Status,
)
from heinzel.kernel._module import Module
from heinzel.kernel._progress import Progress
from heinzel.kernel._release import ReleaseCache
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
    "CredentialBackend",
    "CredentialLet",
    "Credentials",
    "DeclaredCredential",
    "EnvironmentBackend",
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
    "ReleaseCache",
    "ServiceLet",
    "Settings",
    "SettingsLet",
    "Status",
    "boot",
    "discover",
    "env_prefix",
    "environment_variable",
    "requirement_from_receipt",
    "user_config_dir",
    "walk",
]
