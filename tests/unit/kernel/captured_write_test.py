"""The captured-write contract defaults to denial and keeps draft contents bounded."""

import pytest
from pydantic import ValidationError

from heinzel import HeinzelError
from heinzel.kernel._captured_write import CapturedWriteDraft, CapturedWriteReview, CapturedWriteService

pytestmark = pytest.mark.unit


def _draft(**changes):
    data = {
        "owner": "u-" + "a" * 32,
        "session": "session-one",
        "request_id": "request-one",
        "provider": "fixture",
        "binding_id": "a" * 64,
        "account": "account-one",
        "source": "source-one",
        "reference": "fixture://document/one",
        "operation": "replace-section",
        "section": "summary",
        "base_revision": "revision-one",
        "write_scope": "fixture-write",
        "content": "private draft content",
    }
    return CapturedWriteDraft.model_validate(data | changes)


def test_default_provider_refuses_and_draft_repr_hides_content():
    draft = _draft()
    assert "private draft content" not in repr(draft)
    with pytest.raises(HeinzelError, match="does not support"):
        CapturedWriteService().open_lease(draft)


@pytest.mark.parametrize("content", ["", "é" * 32769])
def test_rejects_unbounded_or_empty_content(content):
    with pytest.raises(ValidationError):
        _draft(content=content)


def test_review_requires_an_explicit_conditional_mutation():
    with pytest.raises(ValidationError):
        CapturedWriteReview.model_validate(
            {
                "owner": "u-" + "a" * 32,
                "session": "session-one",
                "provider": "fixture",
                "binding_id": "a" * 64,
                "account": "account-one",
                "source": "source-one",
                "reference": "fixture://document/one",
                "operation": "replace-section",
                "section": "summary",
                "base_revision": "revision-one",
                "content_sha256": "0" * 64,
                "write_scope": "fixture-write",
                "conditional_mutation": False,
            }
        )
