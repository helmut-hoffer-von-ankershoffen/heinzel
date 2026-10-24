"""The kernel: discovers modules, collects the lets they advertise and injects services.

The kernel knows nothing about any head. Heads (`heinzel.heads`) ask it for lets and decide how to expose them.
"""

from heinzel.kernel._agents import AgentResult, AgentRunner, Budget, RunStatus, TokenUsage, ToolProfile
from heinzel.kernel._bindings import CapabilityView, Requester
from heinzel.kernel._boot import boot
from heinzel.kernel._capabilities import CAPABILITIES, MODES, Capability, Mode, Requirement
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
    subprocess_environment,
)
from heinzel.kernel._discovery import BUILTIN_PACKAGE, ENTRY_POINT_GROUP, discover, walk
from heinzel.kernel._duration import parse_duration
from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._installation import CORE_DISTRIBUTION, Installation, requirement_from_receipt
from heinzel.kernel._jobs import CompiledJobs
from heinzel.kernel._kernel import Kernel, ModuleRecord, Origin
from heinzel.kernel._lets import (
    CatchUp,
    Check,
    CredentialLet,
    HealthLet,
    InfoLet,
    JobLet,
    Let,
    OperationLet,
    ProviderLet,
    RequirementLet,
    ServiceLet,
    SettingsLet,
    Status,
    schedule_problem,
)
from heinzel.kernel._module import Module
from heinzel.kernel._progress import Progress
from heinzel.kernel._protocols import (
    Calendar,
    Chat,
    CodeHost,
    Document,
    Documents,
    Event,
    Issue,
    Meeting,
    Meetings,
    Message,
    Participant,
    PostedMessage,
    PullRequest,
    SectionReplacement,
    Tracker,
    Transcript,
    TranscriptSegment,
    Window,
)
from heinzel.kernel._release import ReleaseCache, ReleaseSource
from heinzel.kernel._settings import Settings
from heinzel.kernel._text import clean_text

__all__ = [
    "BUILTIN_PACKAGE",
    "CAPABILITIES",
    "CORE_DISTRIBUTION",
    "ENTRY_POINT_GROUP",
    "FILENAME",
    "KERNEL_SECTION",
    "MODES",
    "PROJECT_FILENAME",
    "AgentResult",
    "AgentRunner",
    "Budget",
    "Calendar",
    "Capability",
    "CapabilityView",
    "CatchUp",
    "Chat",
    "Check",
    "CodeHost",
    "CompiledJobs",
    "Config",
    "ConfigFile",
    "CredentialBackend",
    "CredentialLet",
    "Credentials",
    "DeclaredCredential",
    "Document",
    "Documents",
    "EnvironmentBackend",
    "Event",
    "HealthLet",
    "HeinzelError",
    "InfoLet",
    "Installation",
    "Issue",
    "JobLet",
    "Kernel",
    "Let",
    "Meeting",
    "Meetings",
    "Message",
    "Mode",
    "Module",
    "ModuleRecord",
    "ModuleSettings",
    "OperationLet",
    "Origin",
    "Participant",
    "PostedMessage",
    "Progress",
    "ProviderLet",
    "PullRequest",
    "ReleaseCache",
    "ReleaseSource",
    "Requester",
    "Requirement",
    "RequirementLet",
    "RunStatus",
    "SectionReplacement",
    "ServiceLet",
    "Settings",
    "SettingsLet",
    "Status",
    "TokenUsage",
    "ToolProfile",
    "Tracker",
    "Transcript",
    "TranscriptSegment",
    "Window",
    "boot",
    "clean_text",
    "discover",
    "env_prefix",
    "environment_variable",
    "parse_duration",
    "requirement_from_receipt",
    "schedule_problem",
    "subprocess_environment",
    "user_config_dir",
    "walk",
]
