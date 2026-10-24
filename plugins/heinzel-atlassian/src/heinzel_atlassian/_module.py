"""The module declaration and its credential, apart from `__init__.py` so the adapters can import the let."""

from heinzel import Module

module = Module("atlassian", "Read Jira issues and Confluence pages: the tracker and documents capabilities.")

TOKEN = module.credential(
    "atlassian-token",
    purpose="Atlassian API token without scopes (id.atlassian.com/manage-profile/security/api-tokens), sent with "
    "the atlassian.email setting as Basic auth to atlassian.site_url; it acts with that account's permissions in "
    "Jira and Confluence, and the adapter only reads",
)
