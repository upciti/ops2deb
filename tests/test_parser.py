import os

import pytest
from pydantic import ValidationError

from ops2deb.parser import (
    Blueprint,
    Ops2debParserError,
    load_configuration_file,
)


@pytest.fixture
def blueprint(blueprint_factory):
    return blueprint_factory(
        matrix=dict(architectures=["amd64", "armhf"]),
        fetch=dict(
            url="http://{{goarch}}/{{rust_target}}/{{target}}",
            targets=dict(amd64="x86_64"),
        ),
    )


def test_architectures_should_return_lists_of_architectures(blueprint):
    assert blueprint.architectures() == ["amd64", "armhf"]


def test_render_string_target_should_default_to_blueprint_architecture(blueprint):
    blueprint = blueprint.model_copy(update={"architecture": "armhf"})
    assert blueprint.render_string("{{target}}") == "armhf"


@pytest.mark.parametrize(
    "template,result",
    [
        ("{{goarch}}", "amd64"),
        ("{{rust_target}}", "x86_64-unknown-linux-gnu"),
        ("{{target}}", "x86_64"),
    ],
)
def test_render_string__should_evaluate_goarch_and_rust_targets_and_target(
    template, result, blueprint
):
    assert blueprint.render_string(template) == result


def test_render_fetch_url__should_evaluate_goarch_and_rust_targets_and_target(
    blueprint,
):
    assert blueprint.render_fetch_url() == "http://amd64/x86_64-unknown-linux-gnu/x86_64"


def test_render_fetch_urls__should_return_one_url_per_arch_per_version():
    blueprint = Blueprint(
        name="great-app",
        summary="summary",
        matrix=dict(architectures=["amd64", "armhf"], versions=["1.0.0", "1.1.1"]),
        fetch="http://{{version}}/{{goarch}}/app.tar.gz",
    )
    urls = [
        "http://1.0.0/amd64/app.tar.gz",
        "http://1.1.1/amd64/app.tar.gz",
        "http://1.0.0/arm/app.tar.gz",
        "http://1.1.1/arm/app.tar.gz",
    ]
    assert blueprint.render_fetch_urls() == urls


def test_build__should_evaluate_env_jinja_function():
    # Given
    os.environ.update(
        {
            "CI_PROJECT_NAME": "great-app",
            "CI_COMMIT_TAG": "1.2.3",
            "CI_PROJECT_URL": "https://great-app.io",
        }
    )

    blueprint_dict = Blueprint(
        name="{{env('CI_PROJECT_NAME')}}",
        version="{{env('CI_COMMIT_TAG')}}",
        homepage="{{env('CI_PROJECT_URL')}}",
        summary="My great app",
        description="Detailed description of the great app.",
        fetch="http://great-app.io/releases/{{version}}/great-app.tar.gz",
        script=["cp great-app_linux_{{arch}}_{{version}} {{src}}/usr/bin/great-app"],
    )

    # When
    blueprint = Blueprint.build(blueprint_dict)

    # Then
    assert blueprint.name == os.environ["CI_PROJECT_NAME"]
    assert blueprint.version == os.environ["CI_COMMIT_TAG"]
    assert blueprint.homepage == os.environ["CI_PROJECT_URL"]


def test_blueprint__should_have_source_and_destination_attributes_when_install_is_a_string(  # noqa: E501
    blueprint_factory,
):
    blueprint = blueprint_factory(install=["a:b"])
    assert repr(blueprint.install[0]) == "SourceDestinationStr(source=a, destination=b)"


def test_blueprint__should_raise_when_install_is_a_string_and_colon_separator_is_missing(
    blueprint_factory,
):
    with pytest.raises(ValidationError):
        blueprint_factory(install=["invalid_input"])


def test_blueprint__should_raise_when_install_is_a_string_with_more_than_one_separator(
    blueprint_factory,
):
    with pytest.raises(ValidationError):
        blueprint_factory(install=["invalid::input"])


def test_blueprint__should_raise_when_architectures_is_used_with_architecture(
    blueprint_factory,
):
    with pytest.raises(ValidationError):
        blueprint_factory(architecture="amd64", architectures=["amd64"])


def test_blueprint__should_not_raise_when_architectures_is_used_without_architecture(
    blueprint_factory,
):
    with pytest.raises(ValidationError):
        blueprint_factory(architectures=["amd64"])


@pytest.mark.parametrize("revision", ["1", "10", "1test+test~test"])
def test_blueprint__should_not_raise_when_revision_is_a_valid_string(
    revision, blueprint_factory
):
    blueprint_factory(revision=revision)


def test_blueprint__should_raise_when_revision_begins_with_a_0(blueprint_factory):
    with pytest.raises(ValidationError):
        blueprint_factory(revision="0")


def test_blueprint__should_raise_when_versions_is_used_with_version(
    blueprint_factory,
):
    with pytest.raises(ValidationError):
        blueprint_factory(version="1.0.0", versions=["1.0.0"])


def test_build__should_set_version_when_versions_matrix_is_used():
    # Given
    blueprint_dict = dict(
        name="great-app",
        matrix=dict(versions=["1.0.0", "1.0.1"]),
        homepage="http://great-app.io",
        summary="My great app",
        fetch="http://great-app.io/releases/{{version}}/great-app.tar.gz",
        script=["cp great-app_linux_{{arch}}_{{version}} {{src}}/usr/bin/great-app"],
    )

    # When
    blueprint = Blueprint.build(blueprint_dict)

    # Then
    assert blueprint.version == "1.0.1"


def test_blueprint__should_raise_when_versions_matrix_not_used_and_version_field_not_set():  # noqa: E501
    with pytest.raises(ValidationError) as result:
        Blueprint(
            name="great-app",
            summary="My great app",
        )
    assert result.match("Version field is required when versions matrix is not used")


def test_load_configuration_file__should_parse_lockfile_path_in_configuration_first_line(
    configuration_path,
):
    configuration = """\
    # lockfile=mylockfile.yml
    name: great-app
    version: 1.0.0
    summary: this and that
    """
    configuration_path.write_text(configuration)
    lockfile_path = load_configuration_file(configuration_path).lockfile_path
    assert lockfile_path == configuration_path.parent / "mylockfile.yml"


def test_load_configuration_file__raises_when_file_does_not_exist(configuration_path):
    # When
    with pytest.raises(Ops2debParserError) as error:
        load_configuration_file(configuration_path)

    # Then
    assert error.match("File not found")


def test_load_configuration_file__raises_when_path_is_a_directory(tmp_path):
    # When
    with pytest.raises(Ops2debParserError) as error:
        load_configuration_file(tmp_path)

    # Then
    assert error.match("Path points to a directory")


def test_load_configuration_file__raises_when_configuration_file_contains_invalid_yaml(
    configuration_path,
):
    # Given
    configuration_path.write_text("@£¢±")

    # When
    with pytest.raises(Ops2debParserError) as error:
        load_configuration_file(configuration_path)

    # Then
    assert error.match("Failed to parse")
