import os


def test_parser_should_allow_env_jinja_function_in_string_attributes(blueprint_factory):
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
        summary="awesome summary",
        description="Great description.",
    )
    assert blueprint.name == os.environ["CI_PROJECT_NAME"]
    assert blueprint.version == os.environ["CI_COMMIT_TAG"]
    assert blueprint.homepage == os.environ["CI_PROJECT_URL"]


def test_parser_should_expose_go_arch_and_rust_targets_in_script_and_fetch(
    blueprint_factory,
):
    blueprint = blueprint_factory(
        script=["{{goarch}}", "{{rust_target}}"],
        fetch={"url": "http://{{goarch}}/{{rust_target}}", "sha256": "deadbeef"},
    )
    assert blueprint.render_script() == ["amd64", "x86_64-unknown-linux-gnu"]
    assert blueprint.render_fetch().url == "http://amd64/x86_64-unknown-linux-gnu"
