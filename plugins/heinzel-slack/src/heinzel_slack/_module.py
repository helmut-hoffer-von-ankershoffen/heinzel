"""The module declaration and its credential, apart from `__init__.py` so the adapter can import the let."""

from heinzel import Module

module = Module("slack", "Read and post Slack channel messages: the chat capability.")

TOKEN = module.credential(
    "slack-token",
    purpose="Slack user token (xoxp-...) of your own Slack app, installed to your workspace with the user token "
    "scopes channels:history, groups:history, channels:read, groups:read, users:read and users:read.email for reading, "
    "plus chat:write only if a publish operation posts; it acts as you, with your access to channels",
)
