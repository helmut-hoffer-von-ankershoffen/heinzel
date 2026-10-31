"""Isolated synthetic-source negative for existing connected-review validation."""

from types import SimpleNamespace

import pytest

import heinzel.modules.heinzel._connected_grants as grants
from heinzel.kernel import ConnectedItemIdentity, ConnectedReadReview
from heinzel.kernel._approval import arguments_hash
from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._source_loading import SourceGuardedConnectedLease

pytestmark = pytest.mark.unit


def test_host_observation_rejects_invalid_provider_account(monkeypatch):
    schema = {"type": "object"}
    valid = ConnectedReadReview(
        owner="u-" + "a" * 32,
        session="session",
        operation="capability-method:mail.messages",
        schema_sha256=arguments_hash(schema),
        source_revision="source",
        account_identity="account",
        provider_scopes=("mail.read",),
        fixed_arguments={},
    )
    bad = valid.model_copy(update={"account_identity": ""})

    class Guard:
        def call(self, fn):
            return fn(SimpleNamespace(review=lambda: bad))

        def close(self):
            pass

    lease = SourceGuardedConnectedLease(Guard(), "source-host")
    subject = SimpleNamespace(
        owner=valid.owner,
        kernel=object(),
        _check_current_config=lambda *_: None,
        _selected=lambda *_: (SimpleNamespace(qualname="provider"), object()),
    )
    monkeypatch.setattr(grants, "validate_capability_read_arguments", lambda *args: args[2])
    monkeypatch.setattr(
        grants,
        "describe_capability_read",
        lambda *_: SimpleNamespace(owner_module="provider", schema=schema, binding_sha256="binding"),
    )
    monkeypatch.setattr(grants, "_open_lease", lambda *_: lease)
    with pytest.raises(HeinzelError):
        grants.ConnectedReadGrants._observe(subject, "session", "mail", "messages", {})


def test_review_detaches_nested_values_and_rejects_nonfinite_or_constructed_invalid():
    from heinzel.modules.heinzel._connected_grants import _review_lease

    schema = {"type": "object"}
    valid = ConnectedReadReview(
        owner="u-" + "a" * 32,
        session="session",
        operation="capability-method:mail.messages",
        schema_sha256=arguments_hash(schema),
        source_revision="source",
        account_identity="account",
        provider_scopes=("mail.read",),
        fixed_arguments={"nested": {"value": "original"}},
    )
    lease = SimpleNamespace(review=lambda: valid)
    copied = _review_lease(lease)
    valid.fixed_arguments["nested"]["value"] = "changed"
    assert copied.fixed_arguments["nested"]["value"] == "original"
    valid.fixed_arguments["bad"] = float("nan")
    with pytest.raises(HeinzelError):
        _review_lease(lease)
    constructed = ConnectedReadReview.model_construct(**{**copied.model_dump(), "account_identity": ""})
    with pytest.raises(HeinzelError):
        _review_lease(SimpleNamespace(review=lambda: constructed))
    assert _review_lease(SimpleNamespace(review=lambda: copied)).account_identity == "account"


def test_guarded_source_review_refuses_mutated_and_sanitizes():
    valid = ConnectedReadReview(
        owner="u-" + "a" * 32,
        session="session",
        operation="capability-method:mail.messages",
        schema_sha256="b" * 64,
        source_revision="source",
        account_identity="account",
        provider_scopes=("mail.read",),
        fixed_arguments={},
    )
    bad = valid.model_copy(update={"account_identity": "", "source_revision": "fixture-private-diagnostic"})

    class Guard:
        def call(self, fn):
            return fn(SimpleNamespace(review=lambda: bad))

        def close(self):
            pass

    with pytest.raises(HeinzelError) as error:
        SourceGuardedConnectedLease(Guard(), "source-host").review()
    assert str(error.value) == "The connected read provider could not be reviewed"
    assert "fixture-private-diagnostic" not in str(error.value)


def test_legitimate_json_and_tuple_reviews_remain_compatible():

    valid = ConnectedReadReview(
        owner="u-" + "a" * 32,
        session="session",
        operation="capability-method:mail.messages",
        schema_sha256="b" * 64,
        source_revision="source",
        account_identity="account",
        account_display="Owner account",
        provider_scopes=("mail.read",),
        fixed_arguments={},
        item_identity=ConnectedItemIdentity(collection=("items",), identity_field="id", revision_field="revision"),
    )
    assert ConnectedReadReview.validate_detached(valid) == valid
    assert ConnectedReadReview.validate_detached(valid.model_dump(mode="json")) == valid
    copied = ConnectedReadReview.validate_detached(valid)
    assert copied.provider_scopes == ("mail.read",)
    assert copied.account_display == "Owner account"
    assert copied.item_identity is not None
    assert copied.item_identity.collection == ("items",)
    assert copied.item_identity.revision_field == "revision"


def test_invalid_provider_model_emits_no_serialization_warning():
    import warnings

    valid = ConnectedReadReview(
        owner="u-" + "a" * 32,
        session="session",
        operation="capability-method:mail.messages",
        schema_sha256="b" * 64,
        source_revision="source",
        account_identity="account",
        provider_scopes=("mail.read",),
        fixed_arguments={},
    )
    invalid = valid.model_copy(update={"account_identity": 987654321})
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        with pytest.raises(HeinzelError, match="could not be reviewed") as failure:
            ConnectedReadReview.validate_detached(invalid)
    assert captured == []
    assert "987654321" not in repr(failure.value)


def test_invalid_method_declaration_emits_no_serialization_warning():
    import warnings

    from heinzel import ConnectedReadMethod

    record = SimpleNamespace(qualname="provider")
    valid = ConnectedReadMethod(capability="mail", method="messages")
    invalid = valid.model_copy(update={"capability": 987654321})
    service = SimpleNamespace(supported_reads=lambda: (invalid,))
    kernel = SimpleNamespace(
        source_discovery=None,
        selected_capability_provider=lambda *_: (record, object()),
        selected_capability_service=lambda *_: (record, service),
    )
    subject = SimpleNamespace(kernel=kernel)
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        with pytest.raises(HeinzelError) as failure:
            grants.ConnectedReadGrants._selected(subject, "mail", "messages")
    assert captured == []
    assert "987654321" not in repr(failure.value)
