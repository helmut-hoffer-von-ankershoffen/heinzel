"""Acme Lisbon, a made-up site of Acme (ADR 0040): it recommends another calendar than the org does."""

from heinzel import Module

module = Module("acme-lisbon", "Acme's Lisbon office: a made-up site for tests.")
module.layer(parent="acme")
module.playbook(
    "How Acme Lisbon meets: the office calendar.",
    defaults={"capabilities.calendar": "acme-lisbon", "capabilities.tracker": "acme"},
)
