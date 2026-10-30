"""Build a host-selected metadata catalog through the system-owned verifier."""

from heinzel import CurrentUser, Kernel
from heinzel.kernel import CoreMetadataVerifier
from heinzel.modules.heinzel._host_tools import HostToolCatalog
from heinzel.modules.heinzel._session_crud import SessionCrud

_OPERATION = "system:modules"


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
    )
