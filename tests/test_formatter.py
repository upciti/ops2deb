import json

import pytest

from ops2deb.formatter import format_blueprint, format_description
from ops2deb.parser import Blueprint

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


def test_format_blueprint_should_remove_default_values():
    raw_blueprint = dict(
        name="great-app",
        version="1.0.0",
        summary="A summary",
        description="A description",
    )
    blueprint = Blueprint(**raw_blueprint)
    raw_blueprint_with_defaults = json.loads(blueprint.json())
    assert "revision" in raw_blueprint_with_defaults
    assert format_blueprint(raw_blueprint_with_defaults) == raw_blueprint


def test_format_blueprint_should_not_remove_field_when_value_is_not_default(
    blueprint_factory,
):
    blueprint = blueprint_factory(revision=2, depends=["test"])
    blueprint = format_blueprint(blueprint.dict())
    assert {"revision", "depends", "fetch", "script"}.issubset(blueprint.keys())


def test_format_blueprint_should_not_render_templated_values(blueprint_factory):
    blueprint = blueprint_factory(version="{{env('TEST', 0)}}", construct=True)
    assert format_blueprint(blueprint.dict())["version"] == "{{env('TEST', 0)}}"


def test_format_blueprint_should_replace_fetch_object_with_string_when_only_key_is_url():
    raw_blueprint = dict(
        name="great-app",
        version="1.0.0",
        summary="A summary",
        fetch=dict(url="http://test/app.tar.gz"),
    )
    assert format_blueprint(raw_blueprint)["fetch"] == "http://test/app.tar.gz"


def test_format_blueprint_should_replace_field_arch_by_architecture():
    raw_blueprint = dict(
        name="great-app",
        version="1.0.0",
        arch="all",
        summary="A summary",
    )
    formatted_blueprint = format_blueprint(raw_blueprint)
    assert formatted_blueprint["architecture"] == "all"
    assert "arch" not in formatted_blueprint
