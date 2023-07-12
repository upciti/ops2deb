import json

import pytest

from ops2deb.formatter import format_blueprint, format_description, sort_blueprints
from ops2deb.parser import Blueprint

description_with_empty_line = """
This thing does the following:

- It does this
- And it does that
"""

description_with_long_line = """\
Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt
"""


def test_format_description__should_only_remove_empty_lines_at_start_or_end():
    result = format_description(description_with_empty_line)
    assert result[0] != "\n"
    assert result[-1] != "\n"
    assert "\n" in result


def test_format_description__should_remove_trailing_spaces():
    lines = description_with_empty_line.split("\n")
    lines = [line + " " for line in lines]
    description_with_trailing_spaces = "\n".join(lines)
    result = format_description(description_with_trailing_spaces).split("\n")
    assert result[0][-1] == ":"
    assert result[1] == ""


def test_format_description__should_wrap_long_lines():
    result = format_description(description_with_long_line)
    assert len(result.split("\n")) == 2


@pytest.mark.parametrize(
    "description", [description_with_empty_line, description_with_long_line]
)
def test_format_description__should_be_idempotent(description):
    result = format_description(description)
    assert format_description(result) == result


def test_format_blueprint__should_remove_default_values():
    raw_blueprint = dict(
        name="great-app",
        version="1.0.0",
        summary="A summary",
        description="A description",
    )
    blueprint = Blueprint(**raw_blueprint)
    raw_blueprint_with_defaults = json.loads(blueprint.model_dump_json())
    assert raw_blueprint_with_defaults["revision"] == "1"
    assert format_blueprint(raw_blueprint_with_defaults) == raw_blueprint


def test_format_blueprint__should_not_remove_field_when_value_is_not_default(
    blueprint_factory,
):
    blueprint = blueprint_factory(revision="2", depends=["test"])
    blueprint = format_blueprint(blueprint.model_dump())
    assert {"revision", "depends", "fetch", "script"}.issubset(blueprint.keys())


def test_format_blueprint__should_not_render_templated_values(blueprint_factory):
    blueprint = blueprint_factory(version="{{env('TEST', 0)}}", construct=True)
    assert format_blueprint(blueprint.model_dump())["version"] == "{{env('TEST', 0)}}"


def test_format_blueprint__replaces_fetch_object_with_string_when_only_key_is_url():
    raw_blueprint = dict(
        name="great-app",
        version="1.0.0",
        summary="A summary",
        fetch=dict(url="http://test/app.tar.gz"),
    )
    assert format_blueprint(raw_blueprint)["fetch"] == "http://test/app.tar.gz"


def test_sort_blueprints__sorts_by_name_and_version_when_blueprint_uses_semver():
    # Given
    blueprint_0 = dict(name="great-app", version="2.0.0", summary="A summary")
    blueprint_1 = dict(name="great-app", version="1.0.0", summary="A summary")
    blueprint_2 = dict(
        name="great-app", matrix=dict(versions=["0.1.0", "0.2.0"]), summary="A summary"
    )

    # When
    result = sort_blueprints([blueprint_0, blueprint_1, blueprint_2])

    # Then
    assert result == [blueprint_2, blueprint_1, blueprint_0]


def test_sort_blueprints__does_not_sort_by_version_when_blueprint_does_not_use_semver():
    # Given
    blueprint_0 = dict(name="great-app", version="2020", summary="A summary")
    blueprint_1 = dict(name="great-app", version="2019", summary="A summary")
    blueprint_2 = dict(name="great-app", version="2023", summary="A summary")

    # When
    result = sort_blueprints([blueprint_0, blueprint_1, blueprint_2])

    # Then
    assert result == [blueprint_0, blueprint_1, blueprint_2]


def test_sort_blueprints__sorts_by_name_version_and_revision_when_revision_is_an_int():
    # Given
    blueprint_0 = dict(name="great-app", version="1.0.0", summary="A summary", revision=2)
    blueprint_1 = dict(name="great-app", version="1.0.0", summary="A summary", revision=3)
    blueprint_2 = dict(name="great-app", version="1.0.0", summary="A summary", revision=1)

    # When
    result = sort_blueprints([blueprint_0, blueprint_1, blueprint_2])

    # Then
    assert result == [blueprint_2, blueprint_0, blueprint_1]


def test_sort_blueprints__does_not_sort_by_revision_when_revision_is_not_an_int():
    # Given
    blueprint_0 = dict(name="great-app", version="1.0.0", summary="summary", revision="c")
    blueprint_1 = dict(name="great-app", version="1.0.0", summary="summary", revision="b")
    blueprint_2 = dict(name="great-app", version="1.0.0", summary="summary", revision="a")

    # When
    result = sort_blueprints([blueprint_0, blueprint_1, blueprint_2])

    # Then
    assert result == [blueprint_0, blueprint_1, blueprint_2]
