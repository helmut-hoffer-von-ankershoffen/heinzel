"""Validate the owner-bound main-writer lease protecting an internal turn.

Grant mutations must take the same writer before changing policy. This lease is not a verifier,
policy grant, or a guarantee for mutation paths that bypass the repository lock.
"""

from collections.abc import Callable

from heinzel import HeinzelError, SessionWriter
from heinzel.modules.heinzel._session_protocol import SessionRepository


class TurnAuthorityFence:
    """Prove that the caller still owns the session's main writer and current policy."""

    def __init__(self, repository: SessionRepository, session: str, writer: SessionWriter) -> None:
        self._repository = repository
        self._session = session
        self._owner = repository.principal_id
        self._writer = writer
        self.check()

    def bind(self, repository: SessionRepository, session: str) -> None:
        """Check exact model identity before attaching this lease.

        Raises:
            HeinzelError: If the model is bound to another owner or session.
        """
        if repository is not self._repository or session != self._session:
            msg = "The model and turn authority do not match"
            raise HeinzelError(msg)
        self.check()

    def check(self, verify_policy: Callable[[], None] | None = None) -> None:
        """Fail closed for another owner, another session, or a released writer.

        Raises:
            HeinzelError: If the lease or policy is no longer valid.
        """
        if self._repository.principal_id != self._owner or not self._repository.is_main_writer(
            self._session, self._writer
        ):
            msg = "The turn no longer holds its owner-bound authority fence"
            raise HeinzelError(msg)
        if verify_policy is not None:
            verify_policy()
