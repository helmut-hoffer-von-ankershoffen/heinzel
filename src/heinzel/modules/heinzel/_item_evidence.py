"""Opaque item evidence from an already approved host read; never performs provider IO."""

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import JsonValue

from heinzel import ConnectedItemIdentity, CurrentUser, HeinzelError
from heinzel.modules.heinzel._session_crud import SessionCrud

_MAX_ITEMS = 100
_MAX_ID_BYTES = 1024
_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


def _fingerprint(key: bytes, value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hmac.new(key, encoded, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class ItemReference:
    """An opaque host-issued reference aligned with one element of the returned item array."""

    index: int
    reference: str


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    """Private host-derived receipt scope and durable turn identity."""

    key: bytes
    scope: str
    turn: str
    call: int


def extract_items(
    spec: ConnectedItemIdentity, value: JsonValue, context: EvidenceContext
) -> list[dict[str, JsonValue]]:
    """Derive bounded keyed identities and turn-bound references from actual returned values.

    Raises:
        HeinzelError: If result structure or identity claims are invalid or ambiguous.
    """
    key, scope, turn, call = context.key, context.scope, context.turn, context.call
    current = value
    for field in spec.collection:
        if not isinstance(current, dict) or field not in current:
            msg = "The source item collection is unavailable"
            raise HeinzelError(msg)
        current = current[field]
    if not isinstance(current, list) or len(current) > _MAX_ITEMS:
        msg = "The source item collection is invalid or too large"
        raise HeinzelError(msg)
    results: list[dict[str, JsonValue]] = []
    seen: set[str] = set()
    for index, item in enumerate(current):
        if not isinstance(item, dict):
            msg = "A source item is invalid"
            raise HeinzelError(msg)
        identity = item.get(spec.identity_field)
        revision = item.get(spec.revision_field) if spec.revision_field else None
        if (
            not isinstance(identity, str)
            or not identity
            or len(identity.encode()) > _MAX_ID_BYTES
            or (
                spec.revision_field is not None
                and (not isinstance(revision, str) or not revision or len(revision.encode()) > _MAX_ID_BYTES)
            )
        ):
            msg = "A source item identity is unavailable"
            raise HeinzelError(msg)
        item_key = _fingerprint(key, ["item-v1", scope, identity])
        if item_key in seen:
            msg = "The source returned an ambiguous duplicate item"
            raise HeinzelError(msg)
        seen.add(item_key)
        revision_key = _fingerprint(key, ["item-revision-v1", item_key, revision]) if revision is not None else None
        ref = _fingerprint(key, ["item-reference-v1", scope, turn, call, index, item_key, revision_key])
        results.append({"index": index, "ref": ref, "item": item_key, "revision": revision_key})
    return results


def verify_item_event(key: bytes, data: dict[str, JsonValue], turn: str) -> list[dict[str, JsonValue]]:
    """Verify bounded item metadata and its host MAC; never trust a journal-supplied fingerprint alone.

    Raises:
        HeinzelError: If evidence is malformed or does not authenticate.
    """
    if set(data) != {"version", "scope", "read_seq", "call", "items", "mac"}:
        msg = "The source item evidence is invalid"
        raise HeinzelError(msg)
    call, read_seq, scope, items, mac = (data["call"], data["read_seq"], data["scope"], data["items"], data["mac"])
    if (
        type(data["version"]) is not int
        or data["version"] != 1
        or type(call) is not int
        or call < 1
        or type(read_seq) is not int
        or read_seq < 1
        or not isinstance(scope, str)
        or not _DIGEST.fullmatch(scope)
        or not isinstance(items, list)
        or len(items) > _MAX_ITEMS
        or not isinstance(mac, str)
        or not hmac.compare_digest(
            mac, _fingerprint(key, ["item-observation-v1", turn, {k: v for k, v in data.items() if k != "mac"}])
        )
    ):
        msg = "The source item evidence is invalid"
        raise HeinzelError(msg)
    validated: list[dict[str, JsonValue]] = []
    for index, item in enumerate(items):
        if (
            not isinstance(item, dict)
            or set(item) != {"index", "ref", "item", "revision"}
            or type(item["index"]) is not int
            or item["index"] != index
        ):
            msg = "The source item evidence is invalid"
            raise HeinzelError(msg)
        for name in ("ref", "item"):
            field = item[name]
            if not isinstance(field, str) or not _DIGEST.fullmatch(field):
                msg = "The source item evidence is invalid"
                raise HeinzelError(msg)
        if item["revision"] is not None and (
            not isinstance(item["revision"], str) or not _DIGEST.fullmatch(item["revision"])
        ):
            msg = "The source item evidence is invalid"
            raise HeinzelError(msg)
        validated.append(item)
    return validated


def dedupe_key(item: dict[str, JsonValue], mode: Literal["item", "revision"]) -> str:
    """Select the host-derived identity or revision fingerprint.

    Raises:
        HeinzelError: If a revision-specific policy lacks a source revision.
    """
    value = item["item"] if mode == "item" else item["revision"]
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        msg = "The source item revision is unavailable"
        raise HeinzelError(msg)
    return value


@dataclass(frozen=True, slots=True)
class ItemLookup:
    """Host-derived owner session, current turn and current approved grant terms."""

    session: str
    turn: str
    reference: str
    terms: dict[str, JsonValue]


def resolve_current_item(
    crud: SessionCrud, current: CurrentUser, lookup: ItemLookup, key: bytes
) -> dict[str, JsonValue]:
    """Resolve only authenticated evidence from a completed approved read in this exact turn.

    Raises:
        HeinzelError: If owner, grant, read provenance, or opaque item reference differs.
    """
    owner = current.context().principal.id
    if current.context().principal != current.owner() or owner != crud.repository.principal_id:
        msg = "The source item is unavailable"
        raise HeinzelError(msg)
    if not _DIGEST.fullmatch(lookup.reference):
        msg = "The source item is unavailable"
        raise HeinzelError(msg)
    expected_scope = _fingerprint(key, ["input-scope-v1", owner, lookup.session, lookup.terms])
    events = crud.repository.control_read(lookup.session).events
    matches: list[dict[str, JsonValue]] = []
    latest = max(
        (
            event.seq
            for event in events
            if event.kind == "input-item.observed"
            and event.turn == lookup.turn
            and event.data.get("scope") == expected_scope
        ),
        default=0,
    )
    if any(
        event.turn == lookup.turn
        and event.kind == "interactive.tool"
        and event.data == {"tool": "connected_read", "status": "requested"}
        and event.seq > latest
        for event in events
    ):
        msg = "The latest source read has no usable item evidence"
        raise HeinzelError(msg)
    for event in events:
        if event.kind != "input-item.observed" or event.turn != lookup.turn:
            continue
        values = verify_item_event(key, event.data, lookup.turn)
        if event.data["scope"] != expected_scope or event.seq != latest:
            continue
        call, read_seq = event.data["call"], event.data["read_seq"]
        expected_key = f"{lookup.turn}-{call}-input"
        requested_key = f"{lookup.turn}-{call}-requested"
        starts = [
            item.seq
            for item in events
            if item.kind == "interactive.started"
            and item.turn == lookup.turn
            and isinstance(item.data.get("message_id"), str)
            and lookup.turn == "interactive-" + hashlib.sha256(str(item.data["message_id"]).encode()).hexdigest()
        ]
        reads = [
            item.seq
            for item in events
            if item.seq == read_seq
            and item.seq < event.seq
            and item.turn == lookup.turn
            and item.kind == "input-snapshot.observed"
            and item.key == expected_key
            and item.data.get("scope") == expected_scope
        ]
        requests = [
            item.seq
            for item in events
            if item.turn == lookup.turn
            and item.key == requested_key
            and item.kind == "interactive.tool"
            and item.data == {"tool": "connected_read", "status": "requested"}
        ]
        if len(starts) != 1 or len(reads) != 1 or len(requests) != 1 or not starts[0] < requests[0] < reads[0]:
            msg = "The source item read provenance is unavailable"
            raise HeinzelError(msg)
        matches.extend(item for item in values if hmac.compare_digest(str(item["ref"]), lookup.reference))
    if len(matches) != 1:
        msg = "The source item is unavailable"
        raise HeinzelError(msg)
    return matches[0]
