"""Build a host-selected metadata catalog through the system-owned verifier."""

import json
from typing import TYPE_CHECKING

from heinzel import CurrentUser, Kernel
from heinzel.kernel import CoreMetadataVerifier
from heinzel.modules.heinzel._host_tools import HostToolCatalog
from heinzel.modules.heinzel._session_crud import SessionCrud

if TYPE_CHECKING:
    from pydantic import JsonValue

_OPERATION = "system:modules"


def _modules_result(text: str, limit: int) -> str:
    """Keep complete module identities and advertised lets, disclosing any rows that cannot fit."""
    rows: list[dict[str, JsonValue]] = json.loads(text)
    modules: list[JsonValue] = []
    projection: dict[str, JsonValue] = {
        "modules": modules,
        "total_modules": len(rows),
        "omitted_modules": len(rows),
        "projection": "Module names and advertised lets; descriptive and installation metadata omitted.",
        "full_listing": "hz system modules --json",
    }
    # Reserve the envelope with the largest possible omitted count, including JSON and UTF-8 overhead.
    used = len(json.dumps(projection, ensure_ascii=False, separators=(",", ":")).encode())
    for row in rows:
        entry = {"qualname": row["qualname"], "lets": row["lets"]}
        size = len(json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode()) + bool(modules)
        if used + size <= limit:
            modules.append(entry)
            used += size
    projection["omitted_modules"] = len(rows) - len(modules)
    return json.dumps(projection, ensure_ascii=False, separators=(",", ":"))


def core_metadata_catalog(kernel: Kernel, current: CurrentUser, crud: SessionCrud, session: str) -> HostToolCatalog:
    """Build the only host-approved core metadata catalog; caller still needs owner and approved turn grant.

    No external provider, account, dynamic argument, plugin operation, write, or publisher proof is admitted.
    """
    identity = kernel.resolve_builtin_service(CoreMetadataVerifier, "system")
    return HostToolCatalog(
        kernel,
        current,
        crud,
        session,
        frozenset({_OPERATION}),
        verified_code=identity,
        code_evidence="Loaded owner-installed core source snapshot; no publisher signature verified.",
        oversized_result=_modules_result,
    )
