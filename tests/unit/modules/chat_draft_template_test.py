"""Chat-only template declarations never enter the legacy workflow template collection."""

from typing import cast

import pytest

from heinzel import ChatDraftTemplate, ContractTemplate, HeinzelError, Module, PlaybookLet
from heinzel.modules.heinzel._playbook_adoption import preview_playbook_draft
from heinzel.modules.heinzel._playbooks import find_template, listing

pytestmark = pytest.mark.unit


def test_chat_only_draft_is_discoverable_but_absent_from_legacy_workflow(make_kernel):
    plugin = Module("fixture", "Fixture")
    plugin.playbook(
        "Fixture",
        templates=[ContractTemplate(name="legacy", text="A legacy workflow contract")],
        chat_drafts=[ChatDraftTemplate(name="draft", text="Inert chat material")],
    )
    kernel = make_kernel(fixture=plugin)
    assert preview_playbook_draft(kernel, "fixture:draft").text == "Inert chat material"
    assert find_template(kernel, "fixture:legacy")[1].text == "A legacy workflow contract"
    with pytest.raises(HeinzelError, match="No template"):
        find_template(kernel, "fixture:draft")
    view = listing(kernel)
    assert [item.template for book in view.playbooks for item in book.templates] == ["fixture:legacy"]


def test_chat_and_legacy_template_names_cannot_collide():
    with pytest.raises(ValueError, match="duplicate template names"):
        PlaybookLet(
            name="fixture",
            summary="Fixture",
            templates=(ContractTemplate(name="same", text="legacy"),),
            chat_drafts=(ChatDraftTemplate(name="same", text="chat"),),
        )


def test_one_shot_draft_iterable_is_preserved_and_discoverable(make_kernel):
    draft = ChatDraftTemplate(name="draft", text="Inert chat material")
    drafts = cast("tuple[ChatDraftTemplate, ...]", iter([draft]))
    playbook = PlaybookLet(name="fixture", summary="Fixture", chat_drafts=drafts)
    assert playbook.chat_drafts == (draft,)
    plugin = Module("fixture", "Fixture")
    plugin.add(playbook)
    assert preview_playbook_draft(make_kernel(fixture=plugin), "fixture:draft").text == draft.text


@pytest.mark.parametrize("text", ["", "x" * 4001, "bad\x00text", "bad\ud800text"])
def test_chat_draft_rejects_invalid_or_unbounded_content(text):
    with pytest.raises(ValueError, match="Chat draft"):
        ChatDraftTemplate(name="draft", text=text)


@pytest.mark.parametrize("value", [None, True, 12, {"text": "unexpected shape"}, " \t\r\n"])
def test_dynamic_plugin_data_must_supply_nonblank_text(value):
    with pytest.raises(ValueError, match="Chat draft"):
        ChatDraftTemplate(name="draft", text=cast("str", value))


def test_unicode_boundary_is_previewed_without_rewriting_its_source(make_kernel):
    text = "\U0001f600" * 4000
    plugin = Module("fixture", "Fixture")
    plugin.add(
        PlaybookLet(name="fixture", summary="Fixture", chat_drafts=(ChatDraftTemplate(name="draft", text=text),))
    )
    draft = preview_playbook_draft(make_kernel(fixture=plugin), "fixture:draft")
    assert draft.text == text
    assert draft.placeholders == ()
