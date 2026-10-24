"""Heinzelmännchen: an open-source, AI-driven operating system for product development teams.

The public API for module and plugin authors is re-exported here, so `from heinzel import Module` is all a plugin needs.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("heinzel")
except PackageNotFoundError:  # pragma: no cover - only when importing from a source tree without installing
    __version__ = "0.0.0"

from heinzel.kernel import (
    CatchUp,
    Check,
    CodeHost,
    Config,
    CredentialLet,
    Credentials,
    Document,
    Documents,
    HealthLet,
    HeinzelError,
    InfoLet,
    Issue,
    JobLet,
    Kernel,
    Let,
    Module,
    ModuleRecord,
    ModuleSettings,
    OperationLet,
    Progress,
    ProviderLet,
    PullRequest,
    RequirementLet,
    SectionReplacement,
    ServiceLet,
    Settings,
    SettingsLet,
    Status,
    Tracker,
    Window,
)

__all__ = [
    "CatchUp",
    "Check",
    "CodeHost",
    "Config",
    "CredentialLet",
    "Credentials",
    "Document",
    "Documents",
    "HealthLet",
    "HeinzelError",
    "InfoLet",
    "Issue",
    "JobLet",
    "Kernel",
    "Let",
    "Module",
    "ModuleRecord",
    "ModuleSettings",
    "OperationLet",
    "Progress",
    "ProviderLet",
    "PullRequest",
    "RequirementLet",
    "SectionReplacement",
    "ServiceLet",
    "Settings",
    "SettingsLet",
    "Status",
    "Tracker",
    "Window",
    "__version__",
]
