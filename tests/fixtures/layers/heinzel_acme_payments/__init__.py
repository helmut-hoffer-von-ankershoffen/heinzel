"""Acme Payments, a made-up org under Acme (ADR 0040): a role and a calendar of its own."""

from heinzel import Module

module = Module("acme-payments", "How Acme Payments works: a made-up org for tests.")
module.layer(parent="acme", roles=["payments-lead"])
module.playbook("How Acme Payments plans: its own calendar.", defaults={"capabilities.calendar": "acme-payments"})
