from ops2deb.formatter import format_description

description_with_empty_line = """\
This thing does the following:

- It does this
- And it does that
"""

description_with_long_line = """\
Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt
"""


def test_format_description_should_preserve_empty_lines():
    result = format_description(description_with_empty_line)
    assert result.split("\n")[1] == ""


def test_format_description_should_be_idempotent():
    result = format_description(description_with_empty_line)
    assert format_description(result) == result


def test_format_description_should_wrap_long_lines():
    result = format_description(description_with_long_line)
    assert len(result.split("\n")) == 3
