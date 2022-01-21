import pytest

from ops2deb.formatter import format_description

description_with_empty_line = """
This thing does the following:

- It does this
- And it does that
"""

description_with_long_line = """\
Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt
"""


def test_format_description_should_only_remove_empty_lines_at_start_or_end():
    result = format_description(description_with_empty_line)
    assert result[0] != "\n"
    assert result[-1] != "\n"
    assert "\n" in result


def test_format_description_should_remove_trailing_spaces():
    lines = description_with_empty_line.split("\n")
    lines = [line + " " for line in lines]
    description_with_trailing_spaces = "\n".join(lines)
    result = format_description(description_with_trailing_spaces).split("\n")
    assert result[0][-1] == ":"
    assert result[1] == ""


def test_format_description_should_wrap_long_lines():
    result = format_description(description_with_long_line)
    assert len(result.split("\n")) == 2


@pytest.mark.parametrize(
    "description", [description_with_empty_line, description_with_long_line]
)
def test_format_description_should_be_idempotent(description):
    result = format_description(description)
    assert format_description(result) == result
