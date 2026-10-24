"""The module declaration, apart from `__init__.py` so the adapters can import it without a cycle.

The plugin declares no credential: acli, Atlassian's CLI, owns authentication (`acli jira auth login`), as an
ordinary employee would use it.
"""

from heinzel import Module

module = Module(
    "atlassian", "Read Jira issues and Confluence pages through acli: the tracker and documents capabilities."
)
