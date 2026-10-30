"""Host-derived item fingerprints retain no raw source identifiers."""

import pytest

from heinzel import ConnectedItemIdentity, HeinzelError
from heinzel.modules.heinzel._input_receipts import _fingerprint
from heinzel.modules.heinzel._item_evidence import EvidenceContext, dedupe_key, extract_items, verify_item_event

pytestmark = pytest.mark.unit


def test_item_refs_are_scoped_to_turn_while_item_and_revision_are_stable():
    spec = ConnectedItemIdentity(collection=("entries",), identity_field="id", revision_field="version")
    assert ConnectedItemIdentity.model_validate(spec.model_dump(mode="json")) == spec
    value = {"entries": [{"id": "private-id", "version": "one"}]}
    a = extract_items(spec, value, EvidenceContext(b"a" * 32, "scope", "turn-one", 1))[0]
    b = extract_items(spec, value, EvidenceContext(b"a" * 32, "scope", "turn-two", 1))[0]
    other = extract_items(spec, value, EvidenceContext(b"a" * 32, "other", "turn-one", 1))[0]
    assert a["ref"] != b["ref"]
    assert a["item"] == b["item"]
    assert a["item"] != other["item"]
    assert "private-id" not in str(a)
    changed = extract_items(
        spec, {"entries": [{"id": "private-id", "version": "two"}]}, EvidenceContext(b"a" * 32, "scope", "turn-one", 1)
    )[0]
    assert dedupe_key(a, "item") == dedupe_key(changed, "item")
    assert dedupe_key(a, "revision") != dedupe_key(changed, "revision")


def test_bad_collection_missing_revision_and_duplicate_ids_fail_closed():
    spec = ConnectedItemIdentity(collection=("items",), identity_field="id", revision_field="rev")
    context = EvidenceContext(b"a" * 32, "scope", "turn", 1)
    for value in ({}, {"items": [{"id": "one"}]}, {"items": [{"id": "one", "rev": "a"}, {"id": "one", "rev": "b"}]}):
        with pytest.raises(HeinzelError):
            extract_items(spec, value, context)


def test_item_event_rejects_tampered_metadata():
    key = b"a" * 32
    spec = ConnectedItemIdentity(collection=(), identity_field="id")
    items = extract_items(spec, [{"id": "one"}], EvidenceContext(key, "a" * 64, "turn", 1))
    data = {"version": 1, "scope": "a" * 64, "read_seq": 1, "call": 1, "items": items}
    data["mac"] = _fingerprint(key, ["item-observation-v1", "turn", data])
    assert verify_item_event(key, data, "turn") == items
    with pytest.raises(HeinzelError):
        verify_item_event(key, {**data, "read_seq": 2}, "turn")
    with pytest.raises(HeinzelError):
        verify_item_event(key, data, "other-turn")
    with pytest.raises(HeinzelError):
        verify_item_event(b"b" * 32, data, "turn")
    invalid = {**data, "items": [{**items[0], "index": False}]}
    invalid["mac"] = _fingerprint(
        key, ["item-observation-v1", "turn", {k: v for k, v in invalid.items() if k != "mac"}]
    )
    with pytest.raises(HeinzelError):
        verify_item_event(key, invalid, "turn")


def test_item_policy_is_new_explicit_variant_and_legacy_serialization_is_unchanged():
    from pydantic import ValidationError

    from heinzel.modules.heinzel._scheduled_notifications import ItemNotificationPolicy, ScheduledNotificationPolicy

    legacy = ScheduledNotificationPolicy(max_per_24h=2, topic_cooldown_seconds=60)
    assert "item_evidence" not in legacy.model_dump(mode="json")
    with pytest.raises(ValidationError):
        ScheduledNotificationPolicy(
            max_per_24h=2, topic_cooldown_seconds=60, item_evidence=ItemNotificationPolicy(deduplicate_by="item")
        )
