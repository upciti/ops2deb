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
