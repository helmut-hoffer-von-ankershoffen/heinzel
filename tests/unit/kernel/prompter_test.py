import pytest

from heinzel import HeinzelError, Prompter
from heinzel.kernel import INTERRUPT, ScriptedPrompter, ScriptExhausted

pytestmark = pytest.mark.unit

ZONES = ["Europe/Berlin", "Asia/Tokyo", "America/New_York"]


def test_the_default_prompter_has_nobody_to_ask():
    prompter = Prompter()
    prompter.say("ignored")
    assert not prompter.interactive
    with pytest.raises(HeinzelError, match="nobody is there to answer"):
        prompter.text("Name")


def test_text_takes_the_default_on_an_empty_answer():
    prompter = ScriptedPrompter(["  "])
    assert prompter.text("Greeting", default="Hello") == "Hello"
    assert prompter.transcript == ["Greeting [Hello]:   "]


def test_text_without_default_asks_again_until_answered():
    prompter = ScriptedPrompter(["", "Ada"])
    assert prompter.text("Name") == "Ada"
    assert prompter.transcript == ["Name: ", "Please answer; there is no default.", "Name: Ada"]


def test_text_shows_the_problem_and_asks_again():
    prompter = ScriptedPrompter(["x", "xyz"])
    assert prompter.text("Code", validate=lambda a: None if len(a) == 3 else "Three letters.") == "xyz"
    assert "Three letters." in prompter.transcript


def test_an_empty_default_is_not_shown():
    prompter = ScriptedPrompter([""])
    assert prompter.text("Filter", default="") == ""
    assert prompter.transcript == ["Filter: "]


def test_secrets_are_never_empty_and_never_recorded():
    prompter = ScriptedPrompter([" ", " s3cret "])
    assert prompter.secret("Token") == " s3cret "  # taken as typed
    assert prompter.transcript == ["Token: ***", "The value is empty; please enter it.", "Token: ***"]


@pytest.mark.parametrize(
    ("answers", "default", "expected"),
    [
        ([""], True, True),
        ([""], False, False),
        (["Y"], False, True),
        (["no"], True, False),
        (["maybe", "y"], False, True),
    ],
)
def test_confirm(answers, default, expected):
    assert ScriptedPrompter(answers).confirm("Go on?", default=default) is expected


def test_confirm_shows_its_default():
    prompter = ScriptedPrompter(["", ""])
    prompter.confirm("Go on?")
    prompter.confirm("Go on?", default=False)
    assert prompter.transcript == ["Go on? [Y/n] ", "Go on? [y/N] "]


def test_number_within_bounds_asks_again_otherwise():
    prompter = ScriptedPrompter(["nine", "25", "-1", "17"])
    assert prompter.number("Ends at", minimum=0, maximum=24) == 17
    assert prompter.transcript.count("Please enter a whole number from 0 to 24.") == 3


def test_number_takes_its_default():
    assert ScriptedPrompter([""]).number("Starts at", default=9) == 9


@pytest.mark.parametrize(
    ("minimum", "maximum", "hint"),
    [(1, None, "Please enter a whole number of at least 1."), (None, 5, "Please enter a whole number of at most 5.")],
)
def test_number_with_one_bound(minimum, maximum, hint):
    prompter = ScriptedPrompter(["x", "3"])
    assert prompter.number("How many", minimum=minimum, maximum=maximum) == 3
    assert hint in prompter.transcript


def test_number_without_bounds():
    prompter = ScriptedPrompter(["x", "-3"])
    assert prompter.number("How many") == -3
    assert "Please enter a whole number." in prompter.transcript


def test_choice_by_number_or_text_listing_the_options():
    prompter = ScriptedPrompter(["asia/tokyo", "3"])
    assert prompter.choice("Zone", ZONES) == "Asia/Tokyo"
    assert prompter.choice("Zone", ZONES) == "America/New_York"
    assert prompter.transcript[:4] == [
        "  1) Europe/Berlin",
        "  2) Asia/Tokyo",
        "  3) America/New_York",
        "Zone: asia/tokyo",
    ]


def test_choice_default_and_unknown_answers():
    prompter = ScriptedPrompter(["7", ""])
    assert prompter.choice("Zone", ZONES, default="Asia/Tokyo") == "Asia/Tokyo"
    assert "'7' is not one of the options; enter a number from 1 to 3." in prompter.transcript
    assert "Zone [2]: " in prompter.transcript


def test_choices_by_numbers_and_texts_without_duplicates():
    prompter = ScriptedPrompter(["3, asia/tokyo, 3"])
    assert prompter.choices("Zones", ZONES) == ["America/New_York", "Asia/Tokyo"]


def test_choices_default_and_unknown_answers():
    prompter = ScriptedPrompter(["1,9", ""])
    assert prompter.choices("Zones", ZONES, default=["Europe/Berlin", "America/New_York"]) == [
        "Europe/Berlin",
        "America/New_York",
    ]
    assert "Zones (numbers separated by commas, or none) [1,3]: " in prompter.transcript
    assert "'9' is not one of the options; enter a number from 1 to 3." in prompter.transcript


@pytest.mark.parametrize(
    ("options", "default", "problem"),
    [([], None, "at least one option"), (ZONES, "Mars/Olympus", "not one of the options")],
)
def test_choices_need_options_and_known_defaults(options, default, problem):
    with pytest.raises(ValueError, match=problem):
        ScriptedPrompter().choice("Zone", options, default=default)
    with pytest.raises(ValueError, match=problem):
        ScriptedPrompter().choices("Zones", options, default=[default] if default else [])


def test_a_script_can_interrupt_like_ctrl_c():
    prompter = ScriptedPrompter([INTERRUPT])
    with pytest.raises(KeyboardInterrupt):
        prompter.text("Name")
    assert prompter.transcript == ["Name: ^C"]


def test_a_script_without_answers_names_the_question():
    with pytest.raises(ScriptExhausted, match="no answer left for 'Name:'"):
        ScriptedPrompter().text("Name")


def test_choices_can_pick_none():
    assert ScriptedPrompter(["None"]).choices("Zones", ZONES, default=ZONES) == []


def answering(presets: dict, *, scope: str = "org", answers: tuple[str, ...] | None = None) -> Prompter:
    """A prompter with preset answers; with `answers`, a person answers the rest."""
    prompter = Prompter() if answers is None else ScriptedPrompter(answers)
    prompter.presets = presets
    prompter.scope = scope
    return prompter


def test_keyed_questions_take_preset_answers_without_asking():
    presets = {"org.handle": "mira", "org.role": "engineer", "org.go": False, "org.port": 8080, "org.zones": ["b", "a"]}
    prompter = answering(presets)
    assert prompter.text("Handle", key="handle") == "mira"
    assert prompter.choice("Role", ["engineer", "principal"], key="role") == "engineer"
    assert prompter.confirm("Go?", key="go") is False
    assert prompter.number("Port", key="port") == 8080
    assert prompter.choices("Zones", ["a", "b"], key="zones") == ["b", "a"]
    assert prompter.answered == presets


def test_an_empty_list_preset_picks_none():
    assert answering({"org.zones": []}).choices("Zones", ["a"], key="zones") == []


def test_a_missing_answer_without_a_person_names_the_key_and_the_file():
    with pytest.raises(HeinzelError, match=r"No answer for org.role \(Role\): add `role = \.\.\.` under \[org\]"):
        answering({}).choice("Role", ["engineer"], key="role")


@pytest.mark.parametrize(
    ("ask", "problem"),
    [
        (lambda p: p.choice("Role", ["engineer"], key="role"), "org.role is invalid: 'ceo' is not one of the options"),
        (lambda p: p.number("Port", maximum=10, key="port"), "org.port is invalid: Please enter a whole number"),
        (lambda p: p.text("Name", key="name"), "org.name is invalid: Please answer; there is no default."),
        (lambda p: p.confirm("Go?", key="go"), "org.go is invalid: Please answer y or n."),
    ],
)
def test_an_invalid_preset_fails_instead_of_asking_again(ask, problem):
    prompter = answering({"org.role": "ceo", "org.port": 99, "org.name": "", "org.go": "maybe"}, answers=())
    with pytest.raises(HeinzelError, match=f"The answer for {problem}"):
        ask(prompter)


def test_a_person_answers_what_is_not_preset_and_keyed_answers_are_kept():
    prompter = answering({"org.handle": "mira"}, answers=("", "x", "Lisbon", ""))
    assert prompter.text("Handle", key="handle") == "mira"
    assert prompter.choice("Role", ["engineer", "principal"], default="principal", key="role") == "principal"
    assert prompter.text("Zone", validate=lambda a: None if len(a) > 1 else "Longer.", key="zone") == "Lisbon"
    assert prompter.text("Unkeyed", default="d") == "d"
    assert prompter.answered == {"org.handle": "mira", "org.role": "principal", "org.zone": "Lisbon"}
    assert "Longer." in prompter.transcript
    assert "  1) engineer" in prompter.transcript  # a preset choice lists no options; an asked one does


def test_without_a_person_a_keyed_question_with_a_default_takes_it():
    prompter = answering({})
    assert prompter.text("Hours", default="09-18", key="working_hours") == "09-18"
    assert prompter.confirm("Go?", default=False, key="go") is False
    assert prompter.choice("Role", ["engineer", "principal"], default="principal", key="role") == "principal"
    assert prompter.answered == {"org.working_hours": "09-18", "org.go": False, "org.role": "principal"}


@pytest.mark.parametrize("key", ["api_token", "password", "client_secret", "TOKEN"])
def test_a_key_naming_a_secret_is_refused(key):
    with pytest.raises(ValueError, match="secrets are never keyed"):
        answering({}).text("Token", key=key)


def test_a_key_merely_containing_a_secret_word_is_fine():
    assert answering({"org.max_tokens": "3"}).text("Most", key="max_tokens") == "3"
    assert answering({"org.tokenizer": "x"}).text("Tokenizer", key="tokenizer") == "x"


def test_a_hint_names_the_flag_that_answers_a_key():
    prompter = answering({})
    prompter.hints = {"org.role": "pass `--role <role>`"}
    with pytest.raises(HeinzelError, match=r"No answer for org.role \(Role\): pass `--role <role>`, or add `role"):
        prompter.text("Role", key="role")


def test_a_step_can_refuse_a_preset_answer():
    prompter = answering({"org.zone": "Atlantis"})
    prompter.text("Zone", key="zone")
    with pytest.raises(HeinzelError, match=r"The answer for org\.zone is invalid: no such zone"):
        prompter.refuse("no such zone", "zone")
    person = ScriptedPrompter(["x"])
    person.text("Zone", key="zone")
    person.refuse("no such zone", "zone")
    assert person.transcript[-1] == "no such zone"


def test_keys_without_a_scope_are_full_keys():
    assert answering({"role": "x"}, scope="").text("Role", key="role") == "x"
