import pytest
from heinzel_atlassian._storage import MAX_INDENT, max_output, storage_to_text

from heinzel import HeinzelError

pytestmark = pytest.mark.unit


def test_headings_paragraphs_breaks_and_inline_formatting():
    storage = "<h1>Plan &amp; scope</h1><p>Hello <strong>world</strong>&nbsp;and <code>x=1</code>.<br/>Next</p>"
    assert storage_to_text(storage) == "# Plan & scope\n\nHello **world** and `x=1`.\nNext\n"


def test_nested_and_ordered_lists():
    storage = "<ul><li>one<ul><li>nested</li></ul></li><li>two</li></ul><ol><li>first</li><li>second</li></ol><p>x</p>"
    assert storage_to_text(storage) == "- one\n  - nested\n- two\n\n1. first\n2. second\n\nx\n"


def test_a_code_macro_keeps_its_cdata_body_and_language():
    storage = (
        '<p>Before</p><ac:structured-macro ac:name="code" ac:schema-version="1">'
        '<ac:parameter ac:name="language">python</ac:parameter><ac:parameter ac:name="title">t</ac:parameter>'
        '<ac:plain-text-body><![CDATA[def f():\n    return "<x>"  # two  spaces]]></ac:plain-text-body>'
        "</ac:structured-macro><p>After</p>"
    )
    assert storage_to_text(storage) == (
        'Before\n\n```python\ndef f():\n    return "<x>"  # two  spaces\n```\n\nAfter\n'
    )


def test_noformat_and_pre_blocks_keep_whitespace():
    storage = (
        '<ac:structured-macro ac:name="noformat"><ac:plain-text-body><![CDATA[a  b]]></ac:plain-text-body>'
        "</ac:structured-macro><pre>c\n  d</pre>"
    )
    assert storage_to_text(storage) == "```\na  b\n```\n\n```\nc\n  d\n```\n"


def test_tables_become_pipe_rows_with_a_header_separator():
    storage = (
        "<table><tbody><tr><th>Name</th><th>Role</th></tr><tr><td>Ada</td><td><p>Eng | lead</p></td></tr>"
        "<tr><td>Grace</td></tr></tbody></table>"
    )
    assert storage_to_text(storage) == "| Name | Role |\n| --- | --- |\n| Ada | Eng \\| lead |\n| Grace | |\n"


def test_a_table_without_header_cells_has_no_separator():
    assert storage_to_text("<table><tr><td>a</td><td>b</td></tr></table><table></table>") == "| a | b |\n"


def test_task_lists_show_their_status():
    storage = (
        "<ac:task-list><ac:task><ac:task-id>1</ac:task-id><ac:task-status>complete</ac:task-status>"
        "<ac:task-body>ship it</ac:task-body></ac:task><ac:task><ac:task-id>2</ac:task-id>"
        "<ac:task-status>incomplete</ac:task-status><ac:task-body>test it</ac:task-body></ac:task></ac:task-list>"
    )
    assert storage_to_text(storage) == "- [x] ship it\n- [ ] test it\n"


def test_links_to_the_web_pages_and_anchors():
    storage = (
        '<p><a href="https://x.example/a">a link</a>, <a href="https://x.example/b">https://x.example/b</a>, '
        '<a href="#top">top</a>, <ac:link><ri:page ri:content-title="Roadmap" /></ac:link>, '
        '<ac:link><ri:page ri:content-title="Specs" /><ac:plain-text-link-body><![CDATA[the specs]]>'
        '</ac:plain-text-link-body></ac:link>, <ac:link><ri:url ri:value="https://x.example/c" />'
        "<ac:link-body>c</ac:link-body></ac:link></p>"
    )
    assert storage_to_text(storage) == (
        "[a link](https://x.example/a), https://x.example/b, top, Roadmap, the specs, [c](https://x.example/c)\n"
    )


def test_images_emoticons_mentions_and_macro_parameters_are_dropped():
    storage = (
        '<p>Hi <ac:image><ri:attachment ri:filename="x.png"/></ac:image><ac:emoticon ac:name="smile"/> '
        '<ac:link><ri:user ri:account-id="5b10ac8d" /></ac:link> there</p>'
        '<ac:structured-macro ac:name="info"><ac:parameter ac:name="title">Note</ac:parameter>'
        "<ac:rich-text-body><p>Careful.</p></ac:rich-text-body></ac:structured-macro>"
        "<script>alert(1)</script><style>p {}</style>"
    )
    assert storage_to_text(storage) == "Hi there\n\nCareful.\n"


def test_rules_quotes_and_italics():
    assert storage_to_text("<p>a</p><hr/><blockquote><p><i>b</i></p></blockquote>") == "a\n\n---\n\n_b_\n"


def test_plain_text_and_empty_bodies():
    assert storage_to_text("just text") == "just text\n"
    assert storage_to_text("") == "\n"
    assert storage_to_text("<![CDATA[raw]]>") == "raw\n"


def test_nested_tasks_are_indented_under_their_task_or_list_item():
    task = "<ac:task><ac:task-status>{}</ac:task-status><ac:task-body>{}</ac:task-body></ac:task>"
    storage = (
        "<ac:task-list>"
        + task.format("complete", "parent<ac:task-list>" + task.format("incomplete", "child") + "</ac:task-list>")
        + task.format("incomplete", "sibling")
        + "</ac:task-list><ul><li>item<ac:task-list>"
        + task.format("incomplete", "in a list")
        + "</ac:task-list></li></ul>"
    )
    assert storage_to_text(storage) == "- [x] parent\n  - [ ] child\n- [ ] sibling\n\n- item\n  - [ ] in a list\n"


def test_indentation_stops_at_the_deepest_level():
    text = storage_to_text("<ul><li>x" * (MAX_INDENT + 10))
    indents = [len(line) - len(line.lstrip(" ")) for line in text.splitlines()]
    assert max(indents) == 2 * MAX_INDENT
    assert len(indents) == MAX_INDENT + 10


def test_a_hundred_thousand_nested_list_items_stay_linear():
    storage = "<ul><li>item" * 100_000
    text = storage_to_text(storage)
    assert len(text) <= max_output(len(storage))
    assert text.count("- item") == 100_000
    assert max(len(line) for line in text.splitlines()) == 2 * MAX_INDENT + len("- item")


def test_twenty_thousand_nested_tasks_stay_linear():
    storage = "<ac:task-list><ac:task><ac:task-status>incomplete</ac:task-status><ac:task-body>t" * 20_000
    text = storage_to_text(storage)
    assert len(text) <= max_output(len(storage))
    assert text.count("- [ ] t") == 20_000
    assert max(len(line) for line in text.splitlines()) == 2 * MAX_INDENT + len("- [ ] t")


def test_text_past_the_ceiling_is_refused():
    with pytest.raises(HeinzelError, match="too large or too deeply nested"):
        storage_to_text("<ul><li>" * 100_000)  # 8 characters in, 34 out per level


def test_the_ceiling_is_four_times_the_body_between_1_mb_and_20_mb():
    assert max_output(10) == 1_000_000
    assert max_output(1_000_000) == 4_000_000
    assert max_output(100_000_000) == 20_000_000


def test_an_unclosed_cell_ends_with_its_table():
    storage = "<table><tr><td>a<td>b</table><p>after</p><table><tr><td>c</td></tr></table>"
    assert storage_to_text(storage) == "| a | b |\n\nafter\n\n| c |\n"


def test_a_link_left_open_in_a_table_doesnt_swallow_what_follows():
    storage = '<table><tr><td><a href="https://x.example">a</td></tr></table><p>after</p></a><p>end</p>'
    assert storage_to_text(storage) == "| a |\n\nafter\n\nend\n"


def test_a_link_opened_in_a_cell_and_closed_after_it_keeps_the_text():
    storage = '<table><tr><td><a href="https://x.example">a</td><td>b</a></td></tr></table>'
    assert storage_to_text(storage) == "| a | b |\n"
    storage = '<p><a href="https://x.example">x<table><tr><td>y</a></td></tr></table></p>'
    assert storage_to_text(storage) == "x\n\n| y |\n"


def test_a_link_wrapping_a_table_keeps_the_table_and_names_the_target():
    storage = '<p>See</p><a href="https://x.example/t"><table><tr><th>h</th></tr><tr><td>v</td></tr></table></a>'
    assert storage_to_text(storage) == "See\n\n| h |\n| --- |\n| v |\n\n<https://x.example/t>\n"
    assert storage_to_text("<a href='#x'><p>a</p><p>b</p></a>") == "a\n\nb\n"


def test_an_unclosed_image_ends_at_the_next_block():
    storage = '<p>a<ac:image><ri:attachment ri:filename="x.png"></p><p>b</p><h2>c</h2></ac:image><p>d</p>'
    assert storage_to_text(storage) == "a\n\nb\n\n## c\n\nd\n"


def test_a_closed_image_still_hides_its_contents():
    assert storage_to_text('<p>a <ac:image><ri:url ri:value="u"/>alt</ac:image>b</p>') == "a b\n"


def test_code_containing_backticks_gets_a_longer_fence():
    storage = (
        '<ac:structured-macro ac:name="code"><ac:plain-text-body><![CDATA[```\nx  y\n````]]></ac:plain-text-body>'
        "</ac:structured-macro><pre>```\na  b</pre><p>c  d</p>"
    )
    assert storage_to_text(storage) == "`````\n```\nx  y\n````\n`````\n\n````\n```\na  b\n````\n\nc d\n"


def test_nested_pre_is_one_block_and_an_unclosed_pre_keeps_its_text():
    assert storage_to_text("<pre>a<pre>b</pre>c</pre>") == "```\nabc\n```\n"
    assert storage_to_text("<table><tr><td><pre>a</td></tr></table></pre>") == "| a |\n"
    assert storage_to_text("<p>x</p><pre>a  b") == "x\n\na b\n"
