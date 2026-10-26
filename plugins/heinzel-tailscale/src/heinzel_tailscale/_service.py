"""Read actual sharing state and compose one explicit action from existing configuration and conditional Serve APIs."""

from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

from heinzel import Config, ConfigEditor, HeinzelError, Settings
from heinzel_tailscale._cli import TailscaleCli
from heinzel_tailscale._localapi import ServeStore
from heinzel_tailscale._lock import SharingLock
from heinzel_tailscale._mapping import Mapping
from heinzel_tailscale._models import SharingIntent, SharingState, TailscaleSettings
from heinzel_tailscale._readiness import SharingReadiness

if TYPE_CHECKING:
    from pydantic import JsonValue


class Sharing:
    """Private sharing facts; reading them never replays a persisted request."""

    def __init__(self, settings: Settings, cli: TailscaleCli, config: Config, readiness: SharingReadiness) -> None:
        """Use the existing profile, CLI and trusted runtime-facts services."""
        self.settings = settings
        self.cli = cli
        self.config = config
        self.readiness = readiness

    def choices(self) -> TailscaleSettings:
        """Read the latest explicit sharing intent without executing it."""
        return self.config.reread().settings(TailscaleSettings, "tailscale")

    def origins(self) -> frozenset[str]:
        """Read the effective origin setting; the head separately reports what it actually loaded."""
        return self.config.reread().settings(Settings, "hz").ui_allowed_origins

    def mapping(self) -> Mapping:
        """Plan only the observed node's own private origin and actual or configured backend port.

        Raises:
            HeinzelError: If the installed client is unavailable, signed out or reports unsafe facts.
        """
        node = self.cli.json(("status", "--json", "--peers=false"))
        if not isinstance(node, dict) or node.get("BackendState") != "Running":
            msg = "Open Tailscale and sign in, then try sharing again."
            raise HeinzelError(msg)
        return self._mapping(cast("dict[str, object]", node))

    def _mapping(self, node: dict[str, object]) -> Mapping:
        own = node.get("Self")
        name = own.get("DNSName") if isinstance(own, dict) else None
        if not isinstance(name, str):
            msg = "Tailscale did not report a node DNS name; check MagicDNS in the Tailscale app."
            raise HeinzelError(msg)
        choices = self.choices()
        # A separate CLI/MCP process cannot prove a new override; preserve the explicit previous target until a web
        # owner binds real facts, rather than silently moving an existing mapping back to the configured default.
        old = choices.intent
        port = self.readiness.runtime.port or (old.backend_port if old and old.enabled else self.settings.ui_port)
        mapping = Mapping.for_node(name, port, choices.https_port)
        certificates = node.get("CertDomains")
        if not isinstance(certificates, list) or mapping.hostname not in certificates:
            msg = "Enable HTTPS certificates for this node in Tailscale's own consent flow, then try sharing again."
            raise HeinzelError(msg)
        return mapping

    def status(self) -> SharingState:
        """Report actual state without logging in, changing configuration or replaying the last requested action."""
        choices = self.choices()
        desired = choices.intent.enabled if choices.intent else False
        if self.cli.executable() is None:
            return SharingState(
                state="unavailable",
                installed=False,
                logged_in=False,
                desired_enabled=desired,
                message="Install Tailscale and sign in before sharing the web app.",
            )
        try:
            node = self.cli.json(("status", "--json", "--peers=false"))
        except HeinzelError as error:
            return SharingState(
                state="error", installed=True, logged_in=False, desired_enabled=desired, message=str(error)
            )
        if not isinstance(node, dict) or node.get("BackendState") != "Running":
            return SharingState(
                state="login-required",
                installed=True,
                logged_in=False,
                desired_enabled=desired,
                message="Open Tailscale and sign in, then try sharing again.",
            )
        try:
            mapping = self._mapping(cast("dict[str, object]", node))
            config = _config(self.cli.json(("serve", "status", "--json")))
            return self._state(mapping, config, desired)
        except HeinzelError as error:
            return SharingState(
                state="blocked", installed=True, logged_in=True, desired_enabled=desired, message=str(error)
            )

    def _state(self, mapping: Mapping, config: dict[str, object], desired: bool) -> SharingState:
        old = self.choices().intent
        if old:
            previous = intent_mapping(old)
            if previous.matches(config):
                mapping = previous
        mapping.enable(config)
        present = mapping.matches(config)
        configured = self.origins()
        expected_origins: list[JsonValue] = []
        expected_origins.extend(sorted(configured))
        apply: dict[str, JsonValue] = (
            {"hz.ui_allowed_origins": expected_origins}
            if self.readiness.runtime.port is not None and configured != self.readiness.runtime.loaded_origins
            else {}
        )
        if not present:
            incomplete = old is not None and old.phase in {"enabling", "disabling"} and mapping.has_handler(config)
            return SharingState(
                state="blocked" if incomplete else "off",
                installed=True,
                logged_in=True,
                desired_enabled=desired,
                origin=mapping.origin,
                target=mapping.target,
                apply_settings=apply,
                message="Sharing is inactive, but its prior change is incomplete. Inspect Tailscale Serve."
                if incomplete
                else "Private web sharing is off.",
            )
        if old is None or old.phase != "owned":
            return SharingState(
                state="blocked",
                installed=True,
                logged_in=True,
                desired_enabled=desired,
                mapped=True,
                origin=mapping.origin,
                target=mapping.target,
                apply_settings=apply,
                message="This mapping is not verified as owned by hz. Inspect Tailscale Serve; it was left untouched.",
            )
        ready = self.readiness.verifies(mapping) and mapping.origin in configured
        return SharingState(
            state="ready" if ready else "apply-required",
            installed=True,
            logged_in=True,
            desired_enabled=desired,
            mapped=True,
            origin=mapping.origin,
            target=mapping.target,
            apply_settings=apply,
            message="Private sharing is ready for an authenticated browser."
            if ready
            else "The request is saved; restart/apply and verify it from the owning web app.",
        )


class SharingActions:
    """An explicit enable/disable gesture changes only the requested mapping and its own origin permission."""

    def __init__(self, sharing: Sharing, store: ServeStore, editor: ConfigEditor, lock: SharingLock) -> None:
        """Compose existing owner-scoped configuration and one conditional local Serve transaction."""
        self.sharing = sharing
        self.store = store
        self.editor = editor
        self.lock = lock

    def enable(self) -> SharingState:
        """Configure private sharing after fresh conflict checks; readiness is confirmed only by a web owner.

        Raises:
            HeinzelError: If another service conflicts, source ownership prevents applying or the result is uncertain.
        """
        with self.lock.hold():
            mapping = self.sharing.mapping()
            choices = self.sharing.choices()
            connection = self.store.connect()
            snapshot = self.store.read(connection)
            planned = snapshot.config
            old = choices.intent
            owned = old is not None and old.phase in {"owned", "disabling"}
            if old is not None and owned:
                previous = intent_mapping(old)
                if previous.origin != mapping.origin:
                    msg = "The sharing origin changed; switch its previous request off before enabling the new origin."
                    raise HeinzelError(msg)
                if previous.backend_port != mapping.backend_port:
                    planned = previous.disable(planned)
            if not owned and mapping.has_handler(planned):
                msg = "This root mapping is not owned by hz; inspect Tailscale Serve before changing it."
                raise HeinzelError(msg)
            planned = mapping.enable(planned)
            added = bool(
                old
                and old.phase in {"enabling", "owned", "disabling"}
                and old.origin == mapping.origin
                and old.added_origin
            )
            intent = SharingIntent(
                enabled=True,
                origin=mapping.origin,
                backend_port=mapping.backend_port,
                https_port=mapping.port,
                added_origin=added,
                phase="owned" if owned and planned == snapshot.config else "enabling",
            )
            self.editor.write("tailscale.intent", intent)
            change = self.editor.change_list("hz.ui_allowed_origins", add=(mapping.origin,))
            intent = intent.model_copy(update={"added_origin": added or mapping.origin in change.added})
            self.editor.write("tailscale.intent", intent)
            if mapping.origin not in self.sharing.origins():
                msg = "A higher-priority configuration source prevents applying the origin; Serve was not changed."
                raise HeinzelError(msg)
            if planned != snapshot.config:
                self.store.write(connection, snapshot, planned)
            if not mapping.matches(self.store.read(connection).config):
                msg = "The exact sharing mapping is not verified; inspect Tailscale before trying again."
                raise HeinzelError(msg)
            self.editor.write("tailscale.intent", intent.model_copy(update={"phase": "owned"}))
            return self.sharing.status()

    def disable(self) -> SharingState:
        """Stop only hz's still-matching mapping, preserving unrelated services and manually approved origins.

        Raises:
            HeinzelError: If the mapping changed or a partial/uncertain result needs inspection.
        """
        with self.lock.hold():
            old = self.sharing.choices().intent
            if old is None or old.phase in {"unowned", "released"}:
                return self.sharing.status()
            mapping = intent_mapping(old)
            connection = self.store.connect()
            snapshot = self.store.read(connection)
            if old.phase == "enabling" and mapping.has_handler(snapshot.config):
                self.editor.write("tailscale.intent", old.model_copy(update={"enabled": False}))
                msg = "The prior enable outcome is uncertain; ownership is unverified. Inspect Tailscale Serve."
                raise HeinzelError(msg)
            planned = mapping.disable(snapshot.config)
            intent = SharingIntent(
                enabled=False,
                origin=mapping.origin,
                backend_port=mapping.backend_port,
                https_port=mapping.port,
                added_origin=old.added_origin,
                phase="disabling",
            )
            self.editor.write("tailscale.intent", intent)
            if planned != snapshot.config:
                self.store.write(connection, snapshot, planned)
            if mapping.has_handler(self.store.read(connection).config):
                msg = "The mapping still exists; sharing is not confirmed off. Inspect Tailscale before trying again."
                raise HeinzelError(msg)
            intent = intent.model_copy(update={"phase": "released"})
            self.editor.write("tailscale.intent", intent)
            if intent.added_origin:
                self.editor.change_list("hz.ui_allowed_origins", remove=(mapping.origin,))
                # Released mappings are never retried; a later manual mapping/grant cannot become ours again.
                self.editor.write("tailscale.intent", intent.model_copy(update={"added_origin": False}))
            return self.sharing.status()


def intent_mapping(intent: SharingIntent) -> Mapping:
    """Reconstruct the exact owned target; a malformed origin never becomes a mutation target.

    Raises:
        HeinzelError: If persisted intent is not the canonical origin of a valid tailnet node.
    """
    try:
        parsed = urlsplit(intent.origin)
    except ValueError as error:
        msg = "The saved sharing origin is invalid; inspect the sharing configuration before changing network settings."
        raise HeinzelError(msg) from error
    mapping = Mapping.for_node(parsed.hostname or "", intent.backend_port, intent.https_port)
    if mapping.origin != intent.origin:
        msg = "The saved sharing origin is invalid; inspect the sharing configuration before changing network settings."
        raise HeinzelError(msg)
    return mapping


def _config(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        msg = "Tailscale returned an unsupported Serve configuration; no changes were made."
        raise HeinzelError(msg)
    return cast("dict[str, object]", value)
