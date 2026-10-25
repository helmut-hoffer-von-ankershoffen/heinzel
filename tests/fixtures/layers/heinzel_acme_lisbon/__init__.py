"""Acme Lisbon, a made-up site of Acme (ADR 0040): it recommends another calendar than the org does."""

from heinzel import Module, Soundscape, Theme
from heinzel.kernel import DEFAULT_THEME

module = Module("acme-lisbon", "Acme's Lisbon office: a made-up site for tests.")
module.layer(parent="acme")
module.playbook(
    "How Acme Lisbon meets: the office calendar.",
    defaults={"capabilities.calendar": "acme-lisbon", "capabilities.tracker": "acme"},
)
# The org and the site both offer `acme-warm` and the team doesn't: a conflict nobody decided (ADR 0044).
module.theme(Theme.model_validate(DEFAULT_THEME.model_dump() | {"name": "acme-warm", "title": "Acme Lisbon warm"}))
# The org and the site both offer `acme-warm-radio`: a conflict nobody decided (ADR 0058).
module.soundscape(
    Soundscape.model_validate(
        {
            "name": "acme-warm-radio",
            "title": "Lisbon radio",
            "kind": "stream",
            "stream": {"provider": "youtube", "video": "tRsQsTMvPNg"},
        }
    )
)
