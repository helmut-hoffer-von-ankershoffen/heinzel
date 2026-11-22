"""Kept old path: `heinzel.heads._mcp_tokens` moved to the client context in #895 (0.9.54).

A published `hz system serve` up to 0.9.53 imports `tokens_for_user` from here inside a function. If it keeps running
across an in-place update, that import loads the new tree, so this path must keep answering. Remove it once no
supported version imports it (`tests/unit/cross_version_imports_test.py` reports the import as gone).
"""

from heinzel.modules.system.client import tokens_for_user

__all__ = ["tokens_for_user"]
