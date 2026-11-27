"""Compatibility path for verified uv receipt override removal."""

from heinzel.services.install._receipt_overrides import OVERRIDE_ERROR, without_verified_overrides

__all__ = ["OVERRIDE_ERROR", "without_verified_overrides"]
