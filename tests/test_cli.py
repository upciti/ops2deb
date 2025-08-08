import os

import pytest
from typer.testing import CliRunner

from ops2deb.cli import app

runner = CliRunner()


def test_app_should_exit_with_error_when_subcommand_does_not_exist():
    result = runner.invoke(app, ["not-a-subcommand"], catch_exceptions=False)
    assert result.exit_code != 0


def test_app_should_exit_with_error_when_option_does_not_exist():
    result = runner.invoke(app, ["--not-an-option"], catch_exceptions=False)
    assert result.exit_code != 0


def test_app_should_exit_with_0_when_help_option_is_used():
    result = runner.invoke(app, ["--help"], catch_exceptions=False)
    assert result.exit_code == 0


@pytest.mark.parametrize("args", [[], ["-v"], ["-v", "-e", "10"]])
def test_app_should_call_default_subcommand_when_no_subcommand_is_used(args, tmp_path):
    os.environ["OPS2DEB_CONFIG"] = str(tmp_path)
    result = runner.invoke(app, args, catch_exceptions=False)
    assert "did not match any configuration file" in result.output
