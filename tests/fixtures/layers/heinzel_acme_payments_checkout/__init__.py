"""Acme Payments Checkout, a made-up team at Acme Lisbon (ADR 0040): its own tracker and a role."""

from heinzel import Module

module = Module("acme-payments-checkout", "The Checkout team of Acme Payments: a made-up team for tests.")
module.layer(parent="acme-payments", site="acme-lisbon", roles=["checkout-captain"])
module.playbook(
    "How Checkout tracks its work: the team's own board.",
    defaults={"capabilities.tracker": "acme-payments-checkout"},
)
