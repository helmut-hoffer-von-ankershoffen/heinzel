"""Compatibility path for trusted uv discovery."""

from heinzel.services.install._uv_locate import (
    UV_DOCS,
    find_uv,
    missing_uv_message,
    search_path,
    standard_directories,
    with_search_path,
)

__all__ = ["UV_DOCS", "find_uv", "missing_uv_message", "search_path", "standard_directories", "with_search_path"]
