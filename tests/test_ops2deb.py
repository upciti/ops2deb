import os
from pathlib import Path

import pytest
import ruamel.yaml
from typer.testing import CliRunner

from ops2deb.cli import app
from ops2deb.lockfile import Lock
from ops2deb.parser import load, parse

yaml = ruamel.yaml.YAML(typ="safe")


mock_valid_configuration = """\
- name: awesome-metapackage
  version: "{{env('CI_COMMIT_TAG', '1.0.0')}}"
  architecture: all
  epoch: 1
  summary: Awesome metapackage
  description: A detailed description of the awesome metapackage.
  depends:
    - great-app

- name: great-app
  version: 1.0.0
  architecture: all
  revision: 2
  summary: Great package
  fetch: http://testserver/{{version}}/great-app.tar.gz
  script:
    - mv great-app {{src}}/usr/bin/great-app

- name: super-app
  version: 1.0.0
  architecture: all
  summary: Super package
  description: |-
    A detailed description of the super package
    Lorem ipsum dolor sit amet, consectetur adipiscing elit
  fetch: http://testserver/{{version}}/super-app
  install:
    - path: debian/copyright
      content: 2021 John Doe. All rights reserved.
  script:
    - mv super-app {{src}}/usr/bin/super-app
"""

mock_valid_deprecated_configuration = """\
- name: great-app
  version: 1.0.0
  revision: 2
  arch: all
  summary: Great package
  fetch:
    url: http://testserver/{{version}}/great-app.tar.gz
    sha256: f1be6dd36b503641d633765655e81cdae1ff8f7f73a2582b7468adceb5e212a9
  script:
    - mv great-app {{src}}/usr/bin/great-app

- name: super-app
  version: 1.0.0
  arch: all
  summary: Super package
  description: |-
    A detailed description of the super package
    Lorem ipsum dolor sit amet, consectetur adipiscing elit
  fetch:
    url: http://testserver/{{version}}/super-app
    sha256: 5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03
  install:
    - path: debian/copyright
      content: 2021 John Doe. All rights reserved.
  script:
    - mv super-app {{src}}/usr/bin/super-app
"""

mock_up_to_date_configuration = """\
- name: great-app
  version: 1.1.1
  revision: 2
  architecture: all
  summary: Great package
  description: A detailed description of the great package.
  fetch: http://testserver/{{version}}/great-app.tar.gz
  script:
    - mv great-app {{src}}/usr/bin/great-app
"""

mock_configuration_with_invalid_archive_checksum = """\
- name: bad-app
  version: 1.0.0
  architecture: all
  summary: Bad package
  description: |
    A detailed description of the bad package
  fetch:
    url: http://testserver/{{version}}/great-app.tar.gz
    sha256: deadbeef
  script:
    - mv great-app {{src}}/usr/bin/great-app
"""

mock_configuration_with_archive_not_found = """\
- name: bad-app
  version: 1.0.0
  architecture: all
  summary: Bad package
  description: |
    A detailed description of the bad package
  fetch: http://testserver/{{version}}/not-found.zip
  script:
    - mv great-app {{src}}/usr/bin/great-app
"""

mock_configuration_with_server_error = """\
- name: bad-app
  version: 1.0.0
  summary: Bad package
  description: |
    A detailed description of the bad package
  fetch: http://testserver/{{version}}/bad-app.zip
  script:
    - mv bad-app {{src}}/usr/bin/bad-app
"""

mock_configuration_with_multi_arch_remote_file_and_404_on_one_file = """\
- name: great-app
  summary: Great package
  version: 1.0.0
  architectures:
  - amd64
  - armhf
  - arm64
  description: A detailed description of the great package.
  fetch: http://testserver/{{version}}/great-app-{{arch}}.tar.gz
  script:
  - mv great-app {{src}}/usr/bin/great-app
"""

mock_configuration_single_blueprint_with_fetch = """\
name: great-app
version: 1.0.0
revision: 2
architecture: all
summary: Great package
description: A detailed description of the great package.
fetch: http://testserver/{{version}}/great-app.tar.gz
script:
  - mv great-app {{src}}/usr/bin/great-app
"""

mock_configuration_not_properly_formatted = """\
- name: great-app
  summary: Great package
  revision: 2
  version: 1.0.0
  architecture: all
  description: |
    A detailed description of the great package.
  fetch: http://testserver/{{version}}/great-app.tar.gz
  script:
  - mv great-app {{src}}/usr/bin/great-app
"""

mock_configuration_with_deprecated_multi_arch_remote_file = """\
- name: great-app
  summary: Great package
  version: 1.0.0
  description: A detailed description of the great package.
  fetch:
    url: http://testserver/{{version}}/great-app-{{arch}}.tar.gz
    sha256:
      amd64: f1be6dd36b503641d633765655e81cdae1ff8f7f73a2582b7468adceb5e212a9
      armhf: abb864290dedcd1e06857f33bf03b0875d485c06fce80f86944e6565080b8fb5
  script:
  - mv great-app {{src}}/usr/bin/great-app
"""

mock_configuration_with_multi_arch_remote_file = """\
- name: great-app
  summary: Great package
  version: 1.0.0
  architectures:
  - amd64
  - armhf
  description: A detailed description of the great package.
  fetch: http://testserver/{{version}}/great-app-{{arch}}.tar.gz
  script:
  - mv great-app {{src}}/usr/bin/great-app
"""

mock_invalid_configuration_yaml_error = """\
- name: awesome-metapackage
    version: 1.0.0
"""

mock_invalid_configuration_validation_error = """\
- name: awesome-metapackage
"""


@pytest.fixture
def configuration_path(tmp_path) -> Path:
    return tmp_path / "ops2deb.yml"


@pytest.fixture
def call_ops2deb(
    tmp_path, mock_httpx_client, configuration_path, lockfile_path, mock_lockfile
):
    def _invoke(*args, configuration: str | None = None, write: bool = True):
        runner = CliRunner()
        if write is True:
            configuration_path.write_text(configuration or mock_valid_configuration)
        os.environ.update(
            {
                "OPS2DEB_VERBOSE": "1",
                "OPS2DEB_OUTPUT_DIR": str(tmp_path),
                "OPS2DEB_CACHE_DIR": str(tmp_path / "cache"),
                "OPS2DEB_CONFIG": str(configuration_path),
                "OPS2DEB_LOCKFILE": str(lockfile_path),
                "OPS2DEB_EXIT_CODE": "77",
            }
        )
        return runner.invoke(app, [*args], catch_exceptions=False)

    return _invoke


def test_ops2deb_purge_should_delete_files_in_cache(tmp_path, call_ops2deb):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "test").write_text("test")
    result = call_ops2deb("purge")
    assert result.exit_code == 0
    assert (cache_dir / "test").exists() is False


@pytest.mark.parametrize(
    "configuration", [mock_valid_configuration, mock_valid_deprecated_configuration]
)
def test_ops2deb_generate_should_succeed_with_valid_configuration(
    configuration, tmp_path, call_ops2deb
):
    result = call_ops2deb("generate", configuration=configuration)
    assert (tmp_path / "great-app_1.0.0_all/src/usr/bin/great-app").is_file()
    assert (tmp_path / "great-app_1.0.0_all/debian/control").is_file()
    assert (tmp_path / "super-app_1.0.0_all/debian/copyright").is_file()
    assert result.exit_code == 0


@pytest.mark.parametrize(
    "configuration",
    [
        mock_configuration_with_multi_arch_remote_file,
        mock_configuration_with_deprecated_multi_arch_remote_file,
    ],
)
def test_ops2deb_generate_should_succeed_with_valid_multi_arch_fetch_configuration(
    configuration, tmp_path, call_ops2deb
):
    result = call_ops2deb("generate", configuration=configuration)
    assert result.exit_code == 0
    assert (tmp_path / "great-app_1.0.0_amd64/src/usr/bin/great-app").is_file()
    assert (tmp_path / "great-app_1.0.0_armhf/src/usr/bin/great-app").is_file()


def test_ops2deb_generate_should_not_download_already_cached_archives(call_ops2deb):
    result = call_ops2deb("generate")
    assert "Downloading" in result.stdout
    result = call_ops2deb("generate")
    assert "Downloading" not in result.stdout


def test_ops2deb_generate_should_fail_with_invalid_checksum(call_ops2deb):
    result = call_ops2deb(
        "generate", configuration=mock_configuration_with_invalid_archive_checksum
    )
    assert "Wrong checksum for file great-app.tar.gz" in result.stdout
    assert result.exit_code == 77


def test_ops2deb_generate_should_fail_when_archive_not_found(call_ops2deb):
    result = call_ops2deb(
        "generate", configuration=mock_configuration_with_archive_not_found
    )
    assert "404" in result.stdout
    assert result.exit_code == 77


def test_ops2deb_generate_should_not_generate_packages_already_published_in_debian_repo(
    tmp_path, call_ops2deb
):
    result = call_ops2deb("generate", "-r", "http://deb.wakemeops.com stable")
    assert (tmp_path / "great-app_1.0.0_all/src/usr/bin/great-app").is_file()
    assert (tmp_path / "great-app_1.0.0_all/debian/control").is_file()
    assert (tmp_path / "super-app_1.0.0_all/debian/control").is_file() is False
    assert result.exit_code == 0


def test_ops2deb_generate_should_run_script_from_config_directory_when_blueprint_has_not_fetch_instruction(  # noqa: E501
    tmp_path, call_ops2deb
):
    configuration_without_fetch = """\
    name: cool-app
    version: 1.0.0
    architecture: all
    summary: Cool package
    description: |
      A detailed description of the cool package
    script:
      - install -m 755 cool-app.sh {{src}}/usr/bin/cool-app
    """
    (tmp_path / "cool-app.sh").touch()
    result = call_ops2deb("generate", configuration=configuration_without_fetch)
    assert result.exit_code == 0
    assert (tmp_path / "cool-app_1.0.0_all/src/usr/bin/cool-app").is_file()


def test_ops2deb_generate_should_honor_only_argument(tmp_path, call_ops2deb):
    result = call_ops2deb("generate", "--only", "great-app")
    assert list(tmp_path.glob("*_all")) == [tmp_path / "great-app_1.0.0_all"]
    assert result.exit_code == 0


def test_ops2deb_generate_should_not_crash_when_archive_contains_a_dangling_symlink(
    call_ops2deb,
):
    mock_configuration_with_dangling_symlink_in_archive = """\
    - name: great-app
      summary: Great package
      version: 1.0.0
      description: A detailed description of the great package.
      fetch: http://testserver/{{version}}/dangling-symlink.tar.xz
    """
    result = call_ops2deb(
        "generate", configuration=mock_configuration_with_dangling_symlink_in_archive
    )
    assert result.exit_code == 0


def test_ops2deb_generate_should_set_cwd_variable_to_config_directory_when_blueprint_has_a_fetch_and_path_to_config_is_relative(  # noqa: E501
    tmp_path, call_ops2deb, tmp_working_directory
):
    configuration = """\
    name: great-app
    version: 1.0.0
    summary: Great package
    fetch:
      url: http://testserver/{{version}}/great-app.tar.gz
      sha256: f1be6dd36b503641d633765655e81cdae1ff8f7f73a2582b7468adceb5e212a9
    script:
      - mv great-app {{src}}/usr/bin/great-app
      - cp {{cwd}}/test.conf {{src}}/etc/test.conf
    """
    (tmp_path / "test.conf").touch()
    result = call_ops2deb("generate", "-c", "ops2deb.yml", configuration=configuration)
    assert result.exit_code == 0


def test_ops2deb_build_should_succeed_with_valid_configuration(tmp_path, call_ops2deb):
    call_ops2deb("generate")
    result = call_ops2deb("build")
    assert result.exit_code == 0
    assert (tmp_path / "great-app_1.0.0-2~ops2deb_all.deb").is_file()


def test_ops2deb_build_should_exit_with_error_when_build_fails(tmp_path, call_ops2deb):
    call_ops2deb("generate")
    (tmp_path / "great-app_1.0.0_all/debian/control").write_text("INVALID_CONTROL")
    result = call_ops2deb("build")
    assert result.exit_code == 77


def test_ops2deb_default_should_build_and_generate_packages_when_configuration_is_valid(
    tmp_path, call_ops2deb
):
    result = call_ops2deb()
    assert result.exit_code == 0
    assert (tmp_path / "great-app_1.0.0-2~ops2deb_all.deb").is_file()


def test_ops2deb_update_should_succeed_with_valid_configuration(
    configuration_path, lockfile_path, call_ops2deb
):
    result = call_ops2deb("update")
    configuration = parse(configuration_path)
    lock = Lock(lockfile_path)
    sha256 = "f1be6dd36b503641d633765655e81cdae1ff8f7f73a2582b7468adceb5e212a9"
    assert "great-app can be bumped from 1.0.0 to 1.1.1" in result.stdout
    assert result.exit_code == 0
    assert configuration[1].version == "1.1.1"
    assert lock.sha256("http://testserver/1.1.1/great-app.tar.gz") == sha256


def test_ops2deb_update_should_succeed_with_valid_deprecated_configuration(
    configuration_path, lockfile_path, call_ops2deb
):
    result = call_ops2deb("update", configuration=mock_valid_deprecated_configuration)
    configuration = parse(configuration_path)
    lock = Lock(lockfile_path)
    sha256 = "f1be6dd36b503641d633765655e81cdae1ff8f7f73a2582b7468adceb5e212a9"
    assert "great-app can be bumped from 1.0.0 to 1.1.1" in result.stdout
    assert result.exit_code == 0
    assert configuration[0].version == "1.1.1"
    assert configuration[0].fetch == "http://testserver/{{version}}/great-app.tar.gz"
    assert lock.sha256("http://testserver/1.1.1/great-app.tar.gz") == sha256


def test_ops2deb_update_should_create_summary_when_called_with_output_file(
    tmp_path, call_ops2deb
):
    output_file = tmp_path / "summary.log"
    call_ops2deb(
        "update",
        "--output-file",
        str(output_file),
        configuration=mock_valid_configuration,
    )
    assert output_file.read_text() == "Updated great-app from 1.0.0 to 1.1.1\n"


def test_ops2deb_update_should_create_empty_summary_when_called_with_output_file_and_config_is_up_to_date(  # noqa: E501
    tmp_path, call_ops2deb
):
    output_file = tmp_path / "summary.log"
    call_ops2deb(
        "update",
        "--output-file",
        str(output_file),
        configuration=mock_up_to_date_configuration,
    )
    assert output_file.read_text() == ""


def test_ops2deb_update_should_succeed_with_single_blueprint_configuration(
    configuration_path, call_ops2deb
):
    result = call_ops2deb(
        "update", configuration=mock_configuration_single_blueprint_with_fetch
    )
    configuration = parse(configuration_path)
    assert result.exit_code == 0
    assert configuration[0].version == "1.1.1"


def test_ops2deb_update_should_succeed_with_deprecated_multi_arch_fetch(
    call_ops2deb, configuration_path, lockfile_path
):
    result = call_ops2deb(
        "update", configuration=mock_configuration_with_deprecated_multi_arch_remote_file
    )
    configuration = parse(configuration_path)
    lock = Lock(lockfile_path)
    assert result.exit_code == 0
    assert configuration[0].version == "1.1.1"
    url = "http://testserver/{{version}}/great-app-{{arch}}.tar.gz"
    assert configuration[0].fetch == url
    sha256_armhf = "cf9a3f702d3532d50c5a864285ba60b2d067aea427a770f7267761f69657d746"
    assert lock.sha256("http://testserver/1.1.1/great-app-armhf.tar.gz") == sha256_armhf
    sha256_amd64 = "f1be6dd36b503641d633765655e81cdae1ff8f7f73a2582b7468adceb5e212a9"
    assert lock.sha256("http://testserver/1.1.1/great-app-amd64.tar.gz") == sha256_amd64


def test_ops2deb_update_should_reset_blueprint_revision_to_one(
    configuration_path, call_ops2deb
):
    call_ops2deb("update")
    configuration = load(configuration_path)
    assert "revision" not in configuration[0].keys()
    assert "revision" not in configuration[1].keys()


def test_ops2deb_update_should_fail_gracefully_when_server_error(call_ops2deb):
    result = call_ops2deb("update", configuration=mock_configuration_with_server_error)
    error = "Server error when requesting http://testserver/1.1.0/bad-app.zip"
    assert error in result.stdout
    assert result.exit_code == 77


def test_ops2deb_update_should_fail_gracefully_with_multiarch_blueprint_when_404_error_on_a_file(  # noqa E501
    call_ops2deb,
):
    result = call_ops2deb(
        "update",
        configuration=mock_configuration_with_multi_arch_remote_file_and_404_on_one_file,
    )
    error = "Failed to download http://testserver/1.1.1/great-app-arm64.tar.gz."
    assert error in result.stdout
    assert result.exit_code == 77


def test_ops2deb_update_should_skip_blueprints_when_skip_option_is_used(
    configuration_path, call_ops2deb
):
    result = call_ops2deb("update", "--skip", "great-app", "-s", "super-app")
    assert result.exit_code == 0
    assert configuration_path.read_text() == mock_valid_configuration


def test_ops2deb_format_should_be_idempotent(configuration_path, call_ops2deb):
    call_ops2deb("format", configuration=mock_configuration_not_properly_formatted)
    formatted_configuration = configuration_path.read_text()
    call_ops2deb("format", write=False)
    assert formatted_configuration == configuration_path.read_text()


def test_ops2deb_format_should_not_modify_already_formatted_configuration(
    configuration_path, call_ops2deb
):
    result = call_ops2deb("format")
    assert result.exit_code == 0
    assert configuration_path.read_text() == mock_valid_configuration


def test_ops2deb_format_should_exit_with_error_code_when_file_gets_reformatted(
    call_ops2deb,
):
    result = call_ops2deb(
        "format", configuration=mock_configuration_not_properly_formatted
    )
    assert result.exit_code == 77


def test_ops2deb_lock_should_succeed_when_configuration_file_is_valid(call_ops2deb):
    result = call_ops2deb("lock")
    assert result.exit_code == 0


def test_ops2deb_lock_should_succeed_with_valid_multi_arch_fetch(call_ops2deb):
    result = call_ops2deb(
        "lock", configuration=mock_configuration_with_multi_arch_remote_file
    )
    assert result.exit_code == 0


def test_ops2deb_migrate_should_not_do_anything_when_configuration_has_no_hashes(
    call_ops2deb, configuration_path
):
    result = call_ops2deb("migrate")
    assert configuration_path.read_text() == mock_valid_configuration
    assert result.exit_code == 0


def test_ops2deb_migrate_should_remove_hashes_from_config_file_and_create_lockfile(
    call_ops2deb, configuration_path, lockfile_path
):
    result = call_ops2deb("migrate", configuration=mock_valid_deprecated_configuration)
    assert "sha256" not in configuration_path.read_text()
    assert lockfile_path.exists()
    assert result.exit_code == 77


@pytest.mark.parametrize(
    "subcommand", ["update", "generate", "format", "validate", "lock", "migrate"]
)
def test_ops2deb_should_exit_with_error_code_when_configuration_file_is_invalid(
    call_ops2deb, subcommand
):
    result = call_ops2deb(subcommand, configuration=mock_invalid_configuration_yaml_error)
    assert "Invalid YAML file." in result.stdout
    assert result.exit_code == 77
    result = call_ops2deb(
        subcommand, configuration=mock_invalid_configuration_validation_error
    )
    assert "Invalid configuration file." in result.stdout
    assert result.exit_code == 77
