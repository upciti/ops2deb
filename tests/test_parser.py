import os

import pytest
from pydantic import ValidationError

from ops2deb.parser import Blueprint


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
    blueprint = blueprint.copy(update={"architecture": "armhf"})
    assert blueprint.render_string("{{target}}") == "armhf"


@pytest.mark.parametrize(
    "template,result",
    [
        ("{{goarch}}", "amd64"),
        ("{{rust_target}}", "x86_64-unknown-linux-gnu"),
        ("{{target}}", "x86_64"),
    ],
)
def test_render_string_should_evaluate_goarch_and_rust_targets_and_target(
    template, result, blueprint
):
    assert blueprint.render_string(template) == result


def test_render_fetch_url_should_evaluate_goarch_and_rust_targets_and_target(
    blueprint,
):
    assert blueprint.render_fetch_url() == "http://amd64/x86_64-unknown-linux-gnu/x86_64"


def test_render_fetch_urls_should_return_one_url_per_arch_per_version():
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


def test_blueprint_should_evaluate_env_jinja_function_when_used_in_string_attributes(
    blueprint_factory,
):
    os.environ.update(
        {
            "CI_PROJECT_NAME": "great-app",
            "CI_COMMIT_TAG": "1.2.3",
            "CI_PROJECT_URL": "https://great-app.io",
        }
    )
    blueprint = blueprint_factory(
        name="{{env('CI_PROJECT_NAME')}}",
        version="{{env('CI_COMMIT_TAG')}}",
        homepage="{{env('CI_PROJECT_URL')}}",
    )
    assert blueprint.name == os.environ["CI_PROJECT_NAME"]
    assert blueprint.version == os.environ["CI_COMMIT_TAG"]
    assert blueprint.homepage == os.environ["CI_PROJECT_URL"]


def test_blueprint_should_have_source_and_destination_attributes_when_install_is_a_string(
    blueprint_factory,
):
    blueprint = blueprint_factory(install=["a:b"])
    assert repr(blueprint.install[0]) == "SourceDestinationStr(source=a, destination=b)"


def test_blueprint_should_raise_when_install_is_a_string_and_colon_separator_is_missing(
    blueprint_factory,
):
    with pytest.raises(ValidationError):
        blueprint_factory(install=["invalid_input"])


def test_blueprint_should_raise_when_install_is_a_string_with_more_than_one_separator(
    blueprint_factory,
):
    with pytest.raises(ValidationError):
        blueprint_factory(install=["invalid::input"])


def test_blueprint_should_raise_when_architectures_is_used_with_architecture(
    blueprint_factory,
):
    with pytest.raises(ValidationError):
        blueprint_factory(architecture="amd64", architectures=["amd64"])


def test_blueprint_should_not_raise_when_architectures_is_used_without_architecture(
    blueprint_factory,
):
    with pytest.raises(ValidationError):
        blueprint_factory(architectures=["amd64"])


@pytest.mark.parametrize("revision", ["1", "10", "1test+test~test"])
def test_blueprint_should_not_raise_when_revision_is_a_valid_string(
    revision, blueprint_factory
):
    blueprint_factory(revision=revision)


def test_blueprint_should_raise_when_revision_begins_with_a_0(blueprint_factory):
    with pytest.raises(ValidationError):
        blueprint_factory(revision="0")


def test_blueprint_should_raise_when_versions_is_used_with_version(
    blueprint_factory,
):
    with pytest.raises(ValidationError):
        blueprint_factory(version="1.0.0", versions=["1.0.0"])


def test_blueprint_should_set_version_when_versions_matrix_is_used():
    blueprint = Blueprint(
        matrix=dict(versions=["1.0.0", "1.0.1"]),
        name="great-app",
        summary="My great app",
    )
    assert blueprint.version == "1.0.1"


def test_blueprint_should_raise_when_versions_matrix_not_used_and_version_field_not_set():
    with pytest.raises(ValidationError) as result:
        Blueprint(
            name="great-app",
            summary="My great app",
        )
    assert result.match("Version field is required when versions matrix is not used")
