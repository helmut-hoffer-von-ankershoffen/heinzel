"""The browser's commit gate (ADR 0162): a pure table from the described target to run, commit or never."""

import pytest

from heinzel.modules.ui.browser._gate import Target, commit_name, decide, secret_field, words

pytestmark = pytest.mark.unit


def target(**facts) -> Target:
    return Target.model_validate({"resolved": True, "host": "shop.example.org", "role": "button", **facts})


@pytest.mark.parametrize(
    "name",
    [
        "Send", "SEND MESSAGE", "Post", "Publish now", "Reply", "Buy now", "Place order", "Pay", "Checkout",
        "Check out", "Book", "Transfer", "Delete", "Remove item", "Cancel subscription", "Unsubscribe", "Confirm",
        "Accept all", "I agree", "Sign", "Share", "Invite", "Submit",
        "Senden", "Absenden", "Jetzt kaufen", "Zahlungspflichtig bestellen", "Bezahlen", "Zur Kasse", "Buchen",
        "Überweisen", "Löschen", "LÖSCHEN!", "Entfernen", "Kündigen", "Abbestellen", "Bestätigen", "Akzeptieren",
        "Zustimmen", "Unterschreiben", "Teilen", "Einladen", "Veröffentlichen", "Antworten",
    ],
)  # fmt: skip
def test_a_click_on_a_control_named_with_a_commit_word_is_a_commit(name):
    assert commit_name(name)
    assert decide("click", target(name=name)).outcome == "commit"
    assert decide("click", target(name=name, role="link")).outcome == "commit"


@pytest.mark.parametrize(
    "name", ["Next", "Show more", "Postpone", "Bookmarks", "Signal", "Weiter", "Mehr anzeigen", "Menu", "", "Search"]
)
def test_an_ordinary_control_runs(name):
    assert not commit_name(name)
    assert decide("click", target(name=name)).outcome == "run"


def test_words_are_whole_and_case_folded():
    assert words("Jetzt LÖSCHEN!") == ["jetzt", "löschen"]
    assert words("Straße cvc2") == ["strasse", "cvc2"]


def test_a_submit_control_is_a_commit_whatever_its_name():
    assert decide("click", target(name="Weiter", submit=True)).outcome == "commit"


def test_clicking_into_a_text_field_named_like_a_commit_only_focuses_it():
    assert decide("click", target(name="Reply", role="textbox", editable=True)).outcome == "run"


def test_a_click_by_coordinates_on_an_unresolved_target_is_a_commit():
    assert decide("click", Target(resolved=False)).outcome == "commit"


@pytest.mark.parametrize("action", ["type", "key"])
def test_typing_or_a_key_into_an_unresolved_target_is_refused(action):
    verdict = decide(action, Target(resolved=False), text="a", keys="a")
    assert verdict.outcome == "never"
    assert "take over" in verdict.reason


def test_every_input_on_a_page_with_a_payment_field_is_a_commit():
    page = {"payment_page": True}
    assert decide("click", target(name="Next", **page)).outcome == "commit"
    assert decide("type", target(name="Name", editable=True, **page), text="Mira").outcome == "commit"
    assert decide("key", target(name="Name", editable=True, **page), keys="a").outcome == "commit"


@pytest.mark.parametrize("keys", ["Enter", "enter", "Return", "NumpadEnter", "ctrl+Enter", "shift+Enter"])
def test_enter_in_a_form_field_is_a_commit(keys):
    field = target(role="textbox", name="Message", editable=True)
    assert decide("key", field, keys=keys).outcome == "commit"
    assert decide("key", target(role="checkbox", name="Newsletter", in_form=True), keys=keys).outcome == "commit"


def test_other_keys_and_typing_in_a_form_field_run():
    field = target(role="textbox", name="Message", editable=True, in_form=True)
    assert decide("key", field, keys="ArrowDown").outcome == "run"
    assert decide("key", field, keys="ctrl+a").outcome == "run"
    assert decide("type", field, text="Hello").outcome == "run"


def test_a_new_line_typed_into_a_form_field_is_enter():
    field = target(role="textbox", name="Message", editable=True)
    assert decide("type", field, text="Hello\n").outcome == "commit"


def test_enter_outside_a_form_runs():
    assert decide("key", target(role="RootWebArea", name="News"), keys="Enter").outcome == "run"


@pytest.mark.parametrize("keys", ["Enter", "Space", "shift+Enter"])
def test_enter_or_space_on_a_focused_control_is_judged_like_a_click(keys):
    assert decide("key", target(name="Delete"), keys=keys).outcome == "commit"
    assert decide("key", target(name="Next", submit=True), keys=keys).outcome == "commit"
    assert decide("key", target(name="Next"), keys=keys).outcome == "run"


SECRET_FIELDS = [
    {"type": "password"},
    {"type": "PASSWORD"},
    {"autocomplete": "current-password"},
    {"autocomplete": "section-a new-password"},
    {"autocomplete": "one-time-code"},
    {"autocomplete": "cc-number"},
    {"autocomplete": "billing cc-csc"},
    {"never": True},
    {"never": "one-time-code"},
    {"never": "frame"},
    {"name": "Password"},
    {"name": "Passwort"},
    {"name": "One-time code"},
    {"name": "Verification code"},
    {"name": "Card number"},
    {"name": "Kartennummer"},
    {"name": "CVC"},
    {"name": "TAN"},
]


@pytest.mark.parametrize("facts", SECRET_FIELDS)
def test_nobody_types_into_a_password_one_time_code_or_card_field(facts):
    field = target(role="textbox", editable=True, **facts)
    assert secret_field(field)
    for action, payload in (("type", {"text": "x"}), ("key", {"keys": "a"}), ("key", {"keys": "ctrl+v"})):
        verdict = decide(action, field, **payload)
        assert verdict.outcome == "never"
        assert "ask the person to take over" in verdict.reason


@pytest.mark.parametrize("facts", SECRET_FIELDS)
def test_the_never_list_holds_on_a_payment_page_and_for_enter(facts):
    field = target(role="textbox", editable=True, payment_page=True, in_form=True, **facts)
    assert decide("type", field, text="x").outcome == "never"
    assert decide("key", field, keys="Enter").outcome == "never"


def test_a_secret_field_can_be_clicked_and_left_with_tab_or_escape():
    field = target(role="textbox", editable=True, type="password")
    assert decide("click", field).outcome == "run"
    assert decide("key", field, keys="Tab").outcome == "run"
    assert decide("key", field, keys="shift+Tab").outcome == "run"
    assert decide("key", field, keys="Escape").outcome == "run"


def test_a_button_named_like_a_secret_is_no_secret_field():
    assert not secret_field(target(name="Show password"))


def test_the_target_ignores_facts_it_does_not_know():
    assert Target.model_validate({"resolved": True, "href_host": "example.org", "later": 1}).resolved


# Outside a text field a key may be a shortcut of the site (review C1): only moving keys run without a card.

MAIL_ROW = {"role": "row", "name": "Invoice 4711 from Nordwind", "tag": "tr"}
PAGE_BODY = {"role": "RootWebArea", "name": "Inbox", "tag": "body"}
NOT_EDITABLE = [MAIL_ROW, PAGE_BODY, {"role": "button", "name": "Next"}, {"role": "link", "name": "Older"}]

MOVING_KEYS = [
    "Tab", "shift+Tab", "Escape", "Esc", "PageUp", "PageDown", "Home", "End",
    "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Up", "Down", "Left", "Right", "shift+ArrowDown",
]  # fmt: skip
SHORTCUT_KEYS = [
    "Delete", "Backspace", "e", "E", "l", "y", "x", "1", "F5", "Insert", "shift+Delete", "shift+3",
    "ctrl+Enter", "meta+Enter", "cmd+Enter", "command+Enter", "alt+Enter", "option+Enter", "control+Enter",
    "ctrl+a", "ctrl+s", "ctrl+z", "meta+z", "cmd+Backspace", "alt+ArrowLeft", "ctrl+Tab", "ctrl+shift+Delete",
    "ctrl+Space", "alt+Home",
]  # fmt: skip


@pytest.mark.parametrize("facts", NOT_EDITABLE)
@pytest.mark.parametrize("keys", MOVING_KEYS)
def test_a_key_that_only_moves_runs_outside_a_text_field(facts, keys):
    assert decide("key", target(**facts), keys=keys).outcome == "run"


@pytest.mark.parametrize("facts", NOT_EDITABLE)
@pytest.mark.parametrize("keys", SHORTCUT_KEYS)
def test_any_other_key_or_command_chord_outside_a_text_field_is_a_commit(facts, keys):
    verdict = decide("key", target(**facts), keys=keys)
    assert verdict.outcome == "commit"
    assert "shortcut" in verdict.reason


@pytest.mark.parametrize("keys", ["Enter", "Space", "shift+Enter"])
def test_enter_and_space_keep_their_own_rule_outside_a_text_field(keys):
    assert decide("key", target(**MAIL_ROW), keys=keys).outcome == "run"
    assert decide("key", target(**PAGE_BODY), keys=keys).outcome == "run"
    assert decide("key", target(role="row", name="Delete draft"), keys=keys).outcome == "commit"


@pytest.mark.parametrize("keys", ["Delete", "Backspace", "e", "ctrl+a", "ctrl+z", "meta+ArrowLeft", "x"])
def test_in_a_text_field_keys_edit_the_text_and_run(keys):
    field = target(role="textbox", name="Message", editable=True, in_form=True)
    assert decide("key", field, keys=keys).outcome == "run"
