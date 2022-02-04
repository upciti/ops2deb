import os

import pytest


@pytest.fixture
def mock_blueprint(blueprint_factory):
    return blueprint_factory(
        script=["{{goarch}}", "{{rust_target}}", "{{target}}"],
        fetch=dict(
            url="http://{{goarch}}/{{rust_target}}/{{target}}",
            sha256=dict(amd64="deadbeef", armhf="deadbeef"),
            targets=dict(amd64="x86_64"),
        ),
    )


def test_supported_architectures_should_return_lists_of_archs_from_fetch_sha256(
    mock_blueprint,
):
    assert mock_blueprint.supported_architectures() == ["amd64", "armhf"]


def test_render_script_target_should_default_to_blueprint_arch(mock_blueprint):
    blueprint = mock_blueprint.copy(update={"script": ["{{target}}"], "arch": "armhf"})
    assert blueprint.render_script() == ["armhf"]


def test_render_script_should_evaluate_goarch_and_rust_targets_and_target(mock_blueprint):
    blueprint = mock_blueprint
    assert blueprint.render_script() == ["amd64", "x86_64-unknown-linux-gnu", "x86_64"]


def test_render_fetch_should_evaluate_goarch_and_rust_targets_and_target(mock_blueprint):
    blueprint = mock_blueprint
    assert blueprint.render_fetch().url == "http://amd64/x86_64-unknown-linux-gnu/x86_64"


def test__render_string_attributes_env_jinja_function_should_work_in_string_attributes(
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
