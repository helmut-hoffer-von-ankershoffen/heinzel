"""Acme Payments, a made-up org under Acme (ADR 0040): a role and a calendar of its own."""

from heinzel import Module, Soundscape, Theme
from heinzel.kernel import DEFAULT_THEME

module = Module("acme-payments", "How Acme Payments works: a made-up org for tests.")
module.layer(parent="acme", roles=["payments-lead"])
module.playbook("How Acme Payments plans: its own calendar.", defaults={"capabilities.calendar": "acme-payments"})
# The org and the site both offer `acme-warm` and the team doesn't: a conflict nobody decided (ADR 0044).
module.theme(Theme.model_validate(DEFAULT_THEME.model_dump() | {"name": "acme-warm", "title": "Acme Payments warm"}))
# The org and the site both offer `acme-warm-radio`: a conflict nobody decided (ADR 0058).
module.soundscape(
    Soundscape.model_validate(
        {
            "name": "acme-warm-radio",
            "title": "Payments radio",
            "kind": "stream",
            "stream": {"provider": "youtube", "video": "tRsQsTMvPNg"},
        }
    )
)
