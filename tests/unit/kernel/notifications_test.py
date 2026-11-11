"""Notice identity accepts only bounded personal metadata and remains optional for older stored notices."""

import pytest
from pydantic import ValidationError

from heinzel import NoticeHelper

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "x" * 101},
        {"reason": "x" * 121},
        {"hat": "https://images.example.test/face.svg"},
        {"avatar_url": "https://images.example.test/face.svg"},
    ],
)
def test_helper_metadata_refuses_oversized_text_unknown_fields_and_external_artwork(changes):
    with pytest.raises(ValidationError):
        NoticeHelper.model_validate({"name": "Mira", "hat": "blue", "reason": "scheduled check-in", **changes})
