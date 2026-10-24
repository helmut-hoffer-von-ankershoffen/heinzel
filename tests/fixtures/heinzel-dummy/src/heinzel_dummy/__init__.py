"""Minimal Heinzelmännchen plugin used by Heinzelmännchen's own end-to-end tests."""

from heinzel import Module

module = Module("dummy", "Dummy plugin.")


@module.operation
def hello() -> str:
    """Say hello."""
    return "hello from dummy"


@module.info
def info() -> dict[str, str]:
    return {"greeting": "hello from dummy"}
