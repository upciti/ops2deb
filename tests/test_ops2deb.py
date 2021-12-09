import os
from typing import Optional

import pytest
import ruamel.yaml
from typer.testing import CliRunner

from ops2deb.cli import app
from ops2deb.parser import load, parse

yaml = ruamel.yaml.YAML(typ="safe")


mock_valid_configuration = """
- name: great-app
  version: 1.0.0
  revision: 2
  arch: all
  summary: Great package
  description: |
    A detailed description of the great package
  fetch:
    url: http://testserver/{{version}}/great-app.tar.gz
    sha256: f1be6dd36b503641d633765655e81cdae1ff8f7f73a2582b7468adceb5e212a9
  script:
    - mv great-app {{src}}/usr/bin/great-app

- name: super-app
  version: 1.0.0
  arch: all
  summary: Super package
  description: |
    A detailed description of the super package
  fetch:
    url: http://testserver/{{version}}/super-app.zip
    sha256: 5d5e3a6e8449040d6a25082675295e1aa44b3ea474166c24090d27054a58627a
  script:
    - ls
    - mv great-app {{src}}/usr/bin/great-app
"""

mock_configuration_with_invalid_archive_checksum = """
- name: bad-package
  version: 1.0.0
  arch: all
  summary: Bad package
  description: |
    A detailed description of the bad package
  fetch:
    url: http://testserver/{{version}}/super-app.zip
    sha256: deadbeef
  script:
    - mv great-app {{src}}/usr/bin/great-app
"""


mock_configuration_with_archive_not_found = """
- name: bad-package
  version: 1.0.0
  arch: all
  summary: Bad package
  description: |
    A detailed description of the bad package
  fetch:
    url: http://testserver/{{version}}/not-found.zip
    sha256: deadbeef
  script:
    - mv great-app {{src}}/usr/bin/great-app

- name: great-app
  version: 1.0.0
  arch: all
  summary: Great package
  description: |
    A detailed description of the great package
  fetch:
    url: http://testserver/{{version}}/great-app.tar.gz
    sha256: f1be6dd36b503641d633765655e81cdae1ff8f7f73a2582b7468adceb5e212a9
  script:
    - mv great-app {{src}}/usr/bin/great-app
"""


mock_configuration_with_server_error = """
- name: bad-package
  version: 1.0.0
  summary: Bad package
  description: |
    A detailed description of the bad package
  fetch:
    url: http://testserver/{{version}}/bad-app.zip
    sha256: deadbeef
  script:
    - mv bad-app {{src}}/usr/bin/bad-app
"""


@pytest.fixture(scope="function")
def call_ops2deb(tmp_path, mock_httpx_client):
    def _invoke(*args, configuration: Optional[str] = None):
        runner = CliRunner()
        configuration_path = tmp_path / "ops2deb.yml"
        configuration_path.write_text(configuration or mock_valid_configuration)
        os.environ.update(
            {
                "OPS2DEB_VERBOSE": "1",
                "OPS2DEB_OUTPUT_DIR": str(tmp_path),
                "OPS2DEB_CACHE_DIR": str(tmp_path / "cache"),
                "OPS2DEB_CONFIG": str(configuration_path),
                "OPS2DEB_EXIT_CODE": "77",
            }
        )
        return runner.invoke(app, [*args])

    return _invoke


def test_ops2deb_purge_should_delete_files_in_cache(tmp_path, call_ops2deb):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "test").write_text("test")
    result = call_ops2deb("purge")
    assert result.exit_code == 0
    assert (cache_dir / "test").exists() is False


def test_ops2deb_generate_should_succeed_with_valid_configuration(tmp_path, call_ops2deb):
    result = call_ops2deb("generate")
    print(result.stdout)
    assert (tmp_path / "great-app_1.0.0_all/src/usr/bin/great-app").is_file()
    assert (tmp_path / "great-app_1.0.0_all/debian/control").is_file()
    assert result.exit_code == 0


def test_ops2deb_generate_should_not_download_already_cached_archives(call_ops2deb):
    result = call_ops2deb("generate")
    assert "Downloading" in result.stdout
    result = call_ops2deb("generate")
    assert "Downloading" not in result.stdout


def test_ops2deb_generate_should_fail_with_invalid_checksum(call_ops2deb):
    result = call_ops2deb(
        "generate", configuration=mock_configuration_with_invalid_archive_checksum
    )
    assert "Wrong checksum for file super-app.zip" in result.stdout
    assert result.exit_code == 77


def test_ops2deb_generate_should_fail_when_archive_not_found(tmp_path, call_ops2deb):
    result = call_ops2deb(
        "generate", configuration=mock_configuration_with_archive_not_found
    )
    assert "404" in result.stdout
    assert result.exit_code == 77


def test_ops2deb_generate_should_not_generate_packages_already_published_in_debian_repo(
    tmp_path, call_ops2deb
):
    result = call_ops2deb("generate", "-r", "http://deb.wakemeops.com stable")
    print(result.stdout)
    assert (tmp_path / "great-app_1.0.0_all/src/usr/bin/great-app").is_file()
    assert (tmp_path / "great-app_1.0.0_all/debian/control").is_file()
    assert (tmp_path / "super-app_1.0.0_all/debian/control").is_file() is False
    assert result.exit_code == 0


def test_ops2deb_build_should_succeed_with_valid_configuration(tmp_path, call_ops2deb):
    call_ops2deb("generate")
    result = call_ops2deb("build")
    print(result.stdout)
    assert result.exit_code == 0
    assert (tmp_path / "great-app_1.0.0-2~ops2deb_all.deb").is_file()


def test_ops2deb_update_should_succeed_with_valid_configuration(tmp_path, call_ops2deb):
    result = call_ops2deb("update")
    print(result.stdout)
    configuration = parse(tmp_path / "ops2deb.yml")
    assert "great-app can be bumped from 1.0.0 to 1.1.1" in result.stdout
    assert result.exit_code == 0
    assert configuration[0].version == "1.1.1"


def test_ops2deb_update_should_reset_blueprint_revision_to_one(tmp_path, call_ops2deb):
    call_ops2deb("update")
    configuration = load(tmp_path / "ops2deb.yml", yaml)
    assert configuration[0]["revision"] == 1
    assert "revision" not in configuration[1]


def test_ops2deb_update_should_fail_when_server_error(tmp_path, call_ops2deb):
    result = call_ops2deb("update", configuration=mock_configuration_with_server_error)
    assert "Server error" in result.stdout
    assert result.exit_code == 77
