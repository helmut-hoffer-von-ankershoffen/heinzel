"""Heads: the views on the kernel. Each head exposes the same operations in its own medium.

`cli` is the head that ships today. An MCP head (operations as tools) and a REST head (operations as endpoints)
follow the same recipe: boot the kernel, ask it for `OperationLet`s, derive the interface from their signatures.
"""
