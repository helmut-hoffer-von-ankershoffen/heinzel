"""Host-verifier seam for the built-in metadata operation."""

from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._exposure import Exposed


class CoreMetadataVerifier:
    """Independent verifier supplied by the owner of the core metadata operation."""

    def __call__(self, _item: Exposed) -> str:
        """Refuse unimplemented verification.

        Raises:
            HeinzelError: Always, unless an owning module supplies a verifier.
        """
        msg = "Core metadata verification is unavailable"
        raise HeinzelError(msg)
