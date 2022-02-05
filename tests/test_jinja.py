import os

import pytest

from ops2deb.jinja import environment


@pytest.fixture
def render_string():
    def _render_string(string: str) -> str:
        return environment.from_string(string).render()

    return _render_string


def test_env_function_should_return_env_variable_value_when_it_is_defined(render_string):
    os.environ["SOME_VARIABLE"] = "value"
    assert render_string("{{env('SOME_VARIABLE')}}") == "value"


def test_env_function_should_use_default_value_when_env_variable_is_not_defined(
    render_string,
):
    os.environ.pop("SOME_VARIABLE")
    assert render_string("{{env('SOME_VARIABLE', 'default')}}") == "default"


def test_goarch_filter_should_map_goarch_to_debian_arch(render_string):
    assert render_string("{{ 'armhf' | goarch }}") == "arm"


def test_goarch_filter_should_return_input_when_input_is_not_a_debian_arch(render_string):
    assert render_string("{{ 'input_target' | goarch }}") == "input_target"


def test_rust_target_filter_should_map_rust_targets_to_debian_arch(render_string):
    assert render_string("{{ 'armhf' | rust_target }}") == "arm-unknown-linux-gnueabihf"


def test_rust_target_filter_should_return_input_when_input_is_not_a_debian_arch(
    render_string,
):
    assert render_string("{{ 'input_target' | rust_target }}") == "input_target"
