"""Acme Payments Checkout, a made-up team at Acme Lisbon (ADR 0040): its own tracker, a role and its own look."""

from heinzel import Module, Soundscape, Theme
from heinzel.kernel import DEFAULT_THEME

module = Module("acme-payments-checkout", "The Checkout team of Acme Payments: a made-up team for tests.")
module.layer(parent="acme-payments", site="acme-lisbon", roles=["checkout-captain"])
module.playbook(
    "How Checkout tracks its work: the team's own board.",
    defaults={"capabilities.tracker": "acme-payments-checkout"},
)
# The company's theme's name: the team is the more specific layer, so its look wins (ADR 0044).
module.theme(Theme.model_validate(DEFAULT_THEME.model_dump() | {"name": "acme", "title": "Acme Checkout"}))
# The company's soundscape's name too: the team's wins (ADR 0058).
module.soundscape(
    Soundscape.model_validate(
        {
            "name": "acme-radio",
            "title": "Checkout radio",
            "kind": "stream",
            "stream": {"provider": "youtube", "video": "tRsQsTMvPNg"},
        }
    )
)
