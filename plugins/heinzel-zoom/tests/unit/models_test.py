import pytest
from heinzel_zoom import QUERY, ZoomSettings
from pydantic import ValidationError

pytestmark = pytest.mark.unit


def test_the_default_query_finds_everything_zoom_sends():
    assert ZoomSettings().query == QUERY == "from:zoom.us"


def test_a_query_is_stripped():
    assert ZoomSettings(query="  from:zoom.us label:Meetings ").query == "from:zoom.us label:Meetings"


@pytest.mark.parametrize("query", ["zoom", "to:me@example.com", "from:a.example from:b.example"])
def test_a_query_outside_the_mail_syntax_is_refused_with_what_works(query):
    with pytest.raises(ValidationError, match="Mail query"):
        ZoomSettings(query=query)


def test_a_project_file_cant_choose_which_mail_is_read():
    assert ZoomSettings.ignored_in_project == frozenset({"query"})
