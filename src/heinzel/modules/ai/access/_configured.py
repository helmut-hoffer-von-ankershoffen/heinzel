"""Private continuity binding for the selected model API: the built-in option or a plugin's models (ADR 0159).

Three kinds of binding, by what is selected and configured:

- `ConfiguredApiBinding`: the pinned direct OpenAI model, the one model a dollar-capped grant can price;
- `EndpointApiBinding`: any other `pydantic-ai` configuration, e.g. an Anthropic-compatible endpoint such as a company
  proxy; its fingerprint covers the model, the endpoint, its API and the key's source and value;
- `PluginApiBinding`: a plugin's `ai-models` adapter; its fingerprint covers the plugin, its version and the model,
  because the plugin owns its credentials and hz never sees them.

A fingerprint is not an account identity or a grant. The host must keep the snapshot private, bind it to a real
review/decision when required, and enforce runner, price, budget and revision fences independently at launch.
"""

import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Callable
from typing import Any, Never, SupportsIndex

from heinzel import ConfiguredApiAccess, ConfiguredApiLease, ConfiguredApiSnapshot, CurrentUser, HeinzelError, Kernel
from heinzel.kernel import AiModelsSource, ModelOffer
from heinzel.kernel.ai._ai_models import build_model, choose, list_offers
from heinzel.modules.ai.access._models import AccessSettings
from heinzel.modules.ai.access._pydantic import PydanticAiAccess, build_reserved_openai, selected_access

MODEL = "openai:gpt-4.1-mini-2025-04-14"

_ENDPOINT = "https://api.openai.com/v1"
_KEY_NAME = "api-access-fingerprint-v1"
_INVALID = "The configured API binding is unavailable or changed"
_KEY_BYTES = 32
_MAX_CREDENTIAL_BYTES = 16_384


def _private_key(current: CurrentUser) -> bytes:
    """Create/read an owner-private installation key with lock, atomic publish and no symlink/hardlink following.

    Raises:
        HeinzelError: If the private identity or key is unsafe or damaged.
    """
    current.owner()  # creates and verifies the private identity directory
    directory = os.open(current.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        CurrentUser.verify_private(os.fstat(directory), directory=True)
        lock = CurrentUser.open_private(directory, "owner.lock", os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                handle = CurrentUser.open_private(directory, _KEY_NAME, os.O_RDONLY)
            except FileNotFoundError:
                temporary = f".api-binding-{secrets.token_hex(12)}"
                fd = CurrentUser.open_private(directory, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(secrets.token_bytes(_KEY_BYTES))
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.rename(temporary, _KEY_NAME, src_dir_fd=directory, dst_dir_fd=directory)
                    os.fsync(directory)
                finally:
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(temporary, dir_fd=directory)
                handle = CurrentUser.open_private(directory, _KEY_NAME, os.O_RDONLY)
            with os.fdopen(handle, "rb") as stream:
                key = stream.read(_KEY_BYTES + 1)
            if len(key) != _KEY_BYTES:
                msg = "The private API binding key needs repair"
                raise HeinzelError(msg)
            return key
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)
    finally:
        os.close(directory)


class _ConfiguredApiLease(ConfiguredApiLease):
    """Ephemeral host-only credential lease; never serialize or expose it to a model or API caller."""

    def __init__(
        self,
        check: Callable[[str], None],
        build: Callable[[str], Any],
        snapshot: ConfiguredApiSnapshot,
        credential: str,
    ) -> None:
        self._check = check
        self._build = build
        self._snapshot = snapshot
        self._credential: str | None = credential

    @property
    def snapshot(self) -> ConfiguredApiSnapshot:
        """The secret-free identity corresponding to this exact captured credential."""
        return self._snapshot

    def __repr__(self) -> str:
        return "<ConfiguredApiLease private>"

    def __reduce_ex__(self, _protocol: SupportsIndex) -> Never:
        msg = "A private credential lease cannot be serialized"
        raise TypeError(msg)

    def discard(self) -> None:
        """Drop the local credential reference without launching or building a provider."""
        self._credential = None

    def build_approved(self, approved: ConfiguredApiSnapshot) -> Any:  # noqa: ANN401 - provider is optional dependency
        """Construct with exactly the credential captured for the approved fingerprint, at most once.

        Raises:
            HeinzelError: If the lease, approved snapshot or current host configuration is invalid.
        """
        invalid = "The configured API binding is unavailable or changed"
        credential, self._credential = self._credential, None
        if credential is None or type(approved) is not ConfiguredApiSnapshot or approved != self.snapshot:
            raise HeinzelError(invalid)
        try:
            self._check(approved.owner)
            return self._build(credential)
        except Exception:  # noqa: BLE001 - provider errors may include credential details
            raise HeinzelError(invalid) from None


def _fingerprint(current: CurrentUser, material: list[object]) -> str:
    """A keyed digest of `material` with the owner's private installation key; never reversible to a credential."""
    encoded = json.dumps(material, ensure_ascii=True, separators=(",", ":")).encode()
    return hmac.new(_private_key(current), encoded, hashlib.sha256).hexdigest()


class _LeasedBinding(ConfiguredApiAccess):
    """`capture` and `recheck` in terms of `lease`, the same for every kind of binding."""

    def capture(self) -> ConfiguredApiSnapshot:
        """Return only a secret-free snapshot; no provider is constructed.

        Raises:
            HeinzelError: If current selected access and credentials cannot be verified.
        """
        lease = self.lease()
        snapshot = lease.snapshot
        lease.discard()
        return snapshot

    def recheck(self, snapshot: ConfiguredApiSnapshot) -> None:
        """Reject changed current configuration or resolved credential; this alone does not build a provider.

        Raises:
            HeinzelError: If current access no longer exactly matches the reviewed snapshot.
        """
        if type(snapshot) is not ConfiguredApiSnapshot or self.capture() != snapshot:
            msg = "The configured API binding changed; review current access again"
            raise HeinzelError(msg)


class ConfiguredApiBinding(_LeasedBinding):
    """Trusted host-only resolver for the existing pinned direct configured API access."""

    def __init__(
        self, current: CurrentUser, access: PydanticAiAccess, *, build: Callable[[str], Any] = build_reserved_openai
    ) -> None:
        if type(access) is not PydanticAiAccess:
            msg = "The configured API binding is unavailable or changed"
            raise HeinzelError(msg)
        self._current = current
        self._access = access
        self._build = build

    def _check_configuration(self, owner: str) -> None:
        invalid = "The configured API binding is unavailable or changed"
        if self._current.principal() != self._current.owner() or self._current.principal().id != owner:
            raise HeinzelError(invalid)
        self._access.check_reserved_configuration(MODEL)
        if any(os.environ.get(name) for name in ("OPENAI_CUSTOM_HEADERS", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID")):
            raise HeinzelError(invalid)

    def lease(self) -> ConfiguredApiLease:
        """Capture one credential value and its snapshot for a host-only at-most-once provider build.

        Raises:
            HeinzelError: If selected access, settings, credential or current owner cannot be verified.
        """
        invalid = "The configured API binding is unavailable or changed"
        try:
            owner = self._current.principal().id
            self._check_configuration(owner)

            def capture(credential: str | None, source: str) -> ConfiguredApiLease:
                if not credential or len(credential.encode()) > _MAX_CREDENTIAL_BYTES or not source:
                    raise HeinzelError(invalid)  # noqa: TRY301 - sanitized at the enclosing boundary
                self._check_configuration(owner)
                material = json.dumps(
                    ["api-access-v1", owner, source, credential], ensure_ascii=True, separators=(",", ":")
                )
                fingerprint = hmac.new(_private_key(self._current), material.encode(), hashlib.sha256).hexdigest()
                snapshot = ConfiguredApiSnapshot(owner, "pydantic-ai", MODEL, _ENDPOINT, fingerprint)
                return _ConfiguredApiLease(self._check_configuration, self._build, snapshot, credential)

            return self._access.with_reserved_credential(MODEL, capture)
        except Exception:  # noqa: BLE001 - credential errors may contain private diagnostics
            raise HeinzelError(invalid) from None


class EndpointApiBinding(_LeasedBinding):
    """Trusted host-only resolver for any other configuration of the built-in `pydantic-ai` option (ADR 0159)."""

    def __init__(self, current: CurrentUser, access: PydanticAiAccess) -> None:
        if type(access) is not PydanticAiAccess:
            raise HeinzelError(_INVALID)
        self._current = current
        self._access = access

    def _check_configuration(self, owner: str, model: str) -> None:
        if self._current.principal() != self._current.owner() or self._current.principal().id != owner:
            raise HeinzelError(_INVALID)
        self._access.check_configuration(model)

    def lease(self) -> ConfiguredApiLease:
        """Capture the key (or none, for a local endpoint) and its snapshot, for one build.

        Raises:
            HeinzelError: If selected access, settings, credential or current owner cannot be verified.
        """
        try:
            owner = self._current.principal().id
            model = self._access.configured_model()
            self._check_configuration(owner, model)
            key, source = self._access.configured_credential(self._current, model)
            if key is not None and len(key.encode()) > _MAX_CREDENTIAL_BYTES:
                raise HeinzelError(_INVALID)  # noqa: TRY301 - sanitized at the enclosing boundary
            self._check_configuration(owner, model)
            endpoint, api = self._access.endpoint_identity()
            material: list[object] = ["api-access-v2", owner, "pydantic-ai", model, endpoint, api, source, key]
            snapshot = ConfiguredApiSnapshot(
                owner, "pydantic-ai", model, endpoint, _fingerprint(self._current, material)
            )

            def check(approved_owner: str) -> None:
                self._check_configuration(approved_owner, model)

            def build(captured: str) -> Any:  # noqa: ANN401 - a pydantic_ai Model
                return self._access.build_configured(model, captured or None)

            return _ConfiguredApiLease(check, build, snapshot, key or "")
        except Exception:  # noqa: BLE001 - credential errors may contain private diagnostics
            raise HeinzelError(_INVALID) from None


class PluginApiBinding(_LeasedBinding):
    """Trusted host-only resolver for a plugin's `ai-models` adapter selected as AI access (ADR 0159).

    It is also the model factory `connected_model` takes for that option: `access` and `turn_model`.
    """

    def __init__(
        self,
        current: CurrentUser,
        source: Callable[[], AiModelsSource],
        selected: Callable[[], str | None],
        wanted: Callable[[], str | None],
    ) -> None:
        self._current = current
        self._source = source
        self._selected = selected
        self._wanted = wanted

    @property
    def access(self) -> str:
        """The AI access option: the plugin's adapter name."""
        return self._source().name

    def _owner(self, owner: str | None = None) -> str:
        principal = self._current.principal()
        if principal != self._current.owner() or (owner is not None and principal.id != owner):
            raise HeinzelError(_INVALID)
        return principal.id

    def _choice(self) -> tuple[AiModelsSource, ModelOffer]:
        """The selected plugin and the model it runs now.

        Raises:
            HeinzelError: If the plugin isn't the selected access, or its chosen model is unavailable.
        """
        source = self._source()
        if self._selected() != source.name:
            raise HeinzelError(_INVALID)
        offers, _note = list_offers(source)
        return source, choose(offers, self._wanted())

    @staticmethod
    def _terms(source: AiModelsSource, offer: ModelOffer) -> tuple[str, str, str, str]:
        return source.name, source.distribution, source.version, offer.id

    def _check_terms(self, owner: str, terms: tuple[str, str, str, str]) -> None:
        """Refuse a build when the owner, the selected plugin, its version or its model changed since the lease.

        Raises:
            HeinzelError: If any of them changed.
        """
        self._owner(owner)
        if self._terms(*self._choice()) != terms:
            raise HeinzelError(_INVALID)

    def lease(self) -> ConfiguredApiLease:
        """Capture which plugin, version and model a build would use; the plugin keeps its own credentials.

        Raises:
            HeinzelError: If the selected access, the plugin, its model or the current owner cannot be verified.
        """
        try:
            owner = self._owner()
            source, offer = self._choice()
            terms = self._terms(source, offer)
            material: list[object] = ["api-access-plugin-v1", owner, *terms]
            endpoint = f"plugin:{source.distribution}=={source.version}"
            snapshot = ConfiguredApiSnapshot(
                owner, source.name, offer.id, endpoint, _fingerprint(self._current, material)
            )

            def check(approved_owner: str) -> None:
                self._check_terms(approved_owner, terms)

            return _ConfiguredApiLease(check, lambda _captured: build_model(source, offer), snapshot, "plugin")
        except Exception:  # noqa: BLE001 - a plugin's errors may carry private details
            raise HeinzelError(_INVALID) from None

    def turn_model(self, current: CurrentUser, expected_model: str) -> Any:  # noqa: ANN401 - a pydantic_ai Model
        """Build the plugin's `expected_model` for the owner, if it is the model the selection runs now.

        Raises:
            HeinzelError: If identity, selection or model cannot be established.
        """
        if current is not self._current:
            raise HeinzelError(_INVALID)
        self._owner()
        source, offer = self._choice()
        if offer.id != expected_model:
            msg = "The approved model no longer matches the configured model"
            raise HeinzelError(msg)
        return build_model(source, offer)


def _plugin_model() -> str | None:
    from heinzel import Config  # noqa: PLC0415 - read afresh, like the selection

    return Config.load().settings(AccessSettings, "ai.access").plugin_model


class TrustedConfiguredApiAccess(ConfiguredApiAccess):
    """Resolve the owner adapter through the kernel's built-in factory provenance fence."""

    def __init__(
        self,
        current: CurrentUser,
        kernel: Kernel,
        selected: Callable[[], str | None] = selected_access,
        plugin_model: Callable[[], str | None] = _plugin_model,
    ) -> None:
        self._current = current
        self._kernel = kernel
        self._selected = selected
        self._plugin_model = plugin_model

    def _binding(self) -> ConfiguredApiAccess:
        """The binding of what is selected: a plugin's models, the pinned priced model, or another configuration."""
        selected = self._selected()
        if selected is not None and self._kernel.offers_ai_models(selected):
            name = selected
            return PluginApiBinding(
                self._current, lambda: self._kernel.ai_models(name), self._selected, self._plugin_model
            )
        adapter = self._kernel.resolve_builtin_service(PydanticAiAccess, "ai.access")
        if adapter.pinned(MODEL):
            return ConfiguredApiBinding(self._current, adapter)
        return EndpointApiBinding(self._current, adapter)

    def factory(self) -> "PydanticAiAccess | PluginApiBinding":
        """The model factory of the selected option, for `connected_model`: the built-in one or the plugin's.

        Raises:
            HeinzelError: If the built-in option's provenance can't be verified.
        """
        binding = self._binding()
        if isinstance(binding, PluginApiBinding):
            return binding
        return self._kernel.resolve_builtin_service(PydanticAiAccess, "ai.access")

    def capture(self) -> ConfiguredApiSnapshot:
        """Capture the exact current configured API identity."""
        return self._binding().capture()

    def recheck(self, snapshot: ConfiguredApiSnapshot) -> None:
        """Reject changes to the registered adapter, selection or credential."""
        self._binding().recheck(snapshot)

    def lease(self) -> ConfiguredApiLease:
        """Capture one exact credential for an approved provider request."""
        return self._binding().lease()
