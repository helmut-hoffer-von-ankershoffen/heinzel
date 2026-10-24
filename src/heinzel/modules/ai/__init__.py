"""The ai module: how hz reaches AI (ADR 0057).

Its submodule `access` lists the AI access options installed on this machine (Claude Code, a company gateway, ones
plugins add), says which one is in use and whether each is signed in, tests one on request and switches between them.
"""

from heinzel import Module

module = Module("ai", "How hz reaches AI: the AI access in use, its status and a live test.")

__all__ = ["module"]
