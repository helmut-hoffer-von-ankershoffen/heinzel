"""Read and post Slack channel messages: the chat capability.

A Heinzelmännchen plugin, discovered through the `heinzel.modules` entry point in pyproject.toml. It provides the
adapter `slack` for the capability `chat` in read and write mode (ADR 0028); bind it with `[capabilities] chat =
"slack"` in your user configuration. Only publish operations that declare `chat:write` can post.
"""

from heinzel import Check
from heinzel.kernel import CapabilityView
from heinzel_slack._adapter import Slack
from heinzel_slack._client import SlackError
from heinzel_slack._models import READ_SCOPES, SLACK_API, WRITE_SCOPES, SlackAbout, SlackSettings
from heinzel_slack._module import TOKEN, module
from heinzel_slack._service import SlackService
from heinzel_slack._transport import Response, Transport

HOSTS = ["slack.com", "*.slack.com"]
"""Where data goes: Slack's Web API, which the settings only accept on slack.com."""

module.settings(SlackSettings)
module.service(SlackService)
module.service(Transport)
module.provides("chat", modes={"read", "write"}, hosts=HOSTS)(Slack)


@module.operation
def info(service: SlackService) -> SlackAbout:
    """Show this plugin's version, the Heinzelmännchen it runs on, the Slack API it uses and the scopes it needs."""
    return service.about()


@module.info
def system_info(service: SlackService) -> SlackAbout:
    """Contribute this plugin's details to `hz system info`."""
    return service.about()


@module.health
def health(service: SlackService, view: CapabilityView) -> list[Check]:
    """The API, the token's presence and the binding; never a request to Slack."""
    return service.checks(view)


__all__ = [
    "HOSTS",
    "READ_SCOPES",
    "SLACK_API",
    "TOKEN",
    "WRITE_SCOPES",
    "Response",
    "Slack",
    "SlackAbout",
    "SlackError",
    "SlackService",
    "SlackSettings",
    "Transport",
    "module",
]
