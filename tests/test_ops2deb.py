import os
import shutil
from pathlib import Path

import pytest
import ruamel.yaml
from typer.testing import CliRunner

from ops2deb.cli import app
from ops2deb.lockfile import Lock
from ops2deb.parser import load_configuration_file

yaml = ruamel.yaml.YAML(typ="safe")


mock_valid_configuration = """\
- name: awesome-metapackage
  version: "{{env('CI_COMMIT_TAG', '1.0.0')}}"
  epoch: 1
  architecture: all
  summary: Awesome metapackage
  description: A detailed description of the awesome metapackage.
  depends:
    - great-app

- name: great-app
  version: 1.0.0
  revision: 2
  architecture: all
  homepage: https://geat-app.io
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
  fetch: http://testserver/1.0.0/wrong_checksum-app.tar.gz
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
  fetch: http://testserver/{{version}}/404.zip
"""


mock_configuration_with_multi_arch_remote_file_and_404_on_one_file = """\
- name: great-app
  matrix:
    architectures:
    - amd64
    - armhf
    - arm64
  version: 1.0.0
  summary: Great package
  description: A detailed description of the great package.
  fetch:
    url: http://testserver/{{version}}/great-app-{{target}}.tar.gz
    targets:
      armhf: 404
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

mock_configuration_with_multi_arch_remote_file = """\
- name: great-app
  matrix:
    architectures:
    - amd64
    - armhf
  version: 1.0.0
  summary: Great package
  description: A detailed description of the great package.
  fetch:
    url: http://testserver/{{version}}/great-app-{{arch}}.tar.gz
    targets:
      armhf: armhf
  script:
  - mv great-app {{src}}/usr/bin/great-app
"""

mock_configuration_with_version_matrix = """\
- name: great-app
  matrix:
    versions:
    - 1.0.0
    - 1.0.1
    - 1.1.0
  summary: Great package
  fetch: http://testserver/{{version}}/great-app.tar.gz
  script:
  - mv great-app {{src}}/usr/bin/great-app
"""

mock_configuration_with_lockfile_path = """\
# lockfile=great-app.lock.yml
name: great-app
version: 1.0.0
summary: this is a summary
fetch: http://testserver/{{version}}/great-app.tar.gz
"""


@pytest.fixture
def summary_path(tmp_path) -> Path:
    return tmp_path / "summary.log"


@pytest.fixture
def cache_path(tmp_path) -> Path:
    return tmp_path / "cache"


@pytest.fixture
def call_ops2deb(
    tmp_path,
    configuration_paths,
    lockfile_path,
    cache_path,
    mock_httpx_client,
    mock_lockfile,
):
    def _invoke(
        *args,
        configuration: str | None = None,
        configurations: list[str] | None = None,
        write: bool = True,
    ):
        runner = CliRunner()
        if write is True:
            if configuration is None:
                configuration = mock_valid_configuration
            if configurations is None:
                configurations = [configuration]
            for index, configuration in enumerate(configurations):
                configuration_paths[index].write_text(configuration)

        os.environ.update(
            {
                "OPS2DEB_VERBOSE": "1",
                "OPS2DEB_OUTPUT_DIR": str(tmp_path),
                "OPS2DEB_CACHE_DIR": str(cache_path),
                "OPS2DEB_CONFIG": str(tmp_path / "*.yml"),
                "OPS2DEB_EXIT_CODE": "77",
            }
        )

        result = runner.invoke(app, [*args], catch_exceptions=False)
        print(result.stdout)

        return result

    return _invoke


def test_ops2deb_purge_should_delete_files_in_cache(tmp_path, call_ops2deb):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "test").write_text("test")
    result = call_ops2deb("purge")
    assert result.exit_code == 0
    assert (cache_dir / "test").exists() is False


def test_ops2deb_generate_should_succeed_with_valid_configuration(tmp_path, call_ops2deb):
    result = call_ops2deb("generate", configuration=mock_valid_configuration)
    assert (tmp_path / "great-app_1.0.0_all/src/usr/bin/great-app").is_file()
    assert (tmp_path / "great-app_1.0.0_all/debian/control").is_file()
    assert (tmp_path / "super-app_1.0.0_all/debian/copyright").is_file()
    assert result.exit_code == 0


def test_ops2deb_generate_should_succeed_with_valid_multi_arch_fetch_configuration(
    tmp_path, call_ops2deb
):
    result = call_ops2deb(
        "generate", configuration=mock_configuration_with_multi_arch_remote_file
    )
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
    assert "Wrong checksum for file wrong_checksum-app.tar.gz" in result.stdout
    assert result.exit_code == 77


def test_ops2deb_generate_should_fail_when_archive_not_found(call_ops2deb):
    result = call_ops2deb(
        "generate", configuration=mock_configuration_with_archive_not_found
    )
    expected_error = (
        "Failed to download http://testserver/1.0.0/404.zip. "
        "Server responded with 404."
    )
    assert expected_error in result.stdout
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
    tmp_path, call_ops2deb, tmp_working_directory, configuration_path
):
    configuration = """\
    name: great-app
    version: 1.0.0
    summary: Great package
    fetch: http://testserver/{{version}}/great-app.tar.gz
    script:
      - mv great-app {{src}}/usr/bin/great-app
      - cp {{cwd}}/test.conf {{src}}/etc/test.conf
    """
    (tmp_path / "test.conf").touch()
    result = call_ops2deb(
        "generate", "-c", configuration_path.name, configuration=configuration
    )
    assert result.exit_code == 0


def test_ops2deb_generate_should_not_crash_when_two_blueprints_download_the_same_file(
    tmp_path, call_ops2deb, tmp_working_directory
):
    configuration = """\
    - name: great-app-1
      version: 1.0.0
      summary: Great package
      fetch: http://testserver/{{version}}/great-app.tar.gz
    - name: great-app-2
      version: 1.0.0
      summary: Great package
      fetch: http://testserver/{{version}}/great-app.tar.gz
    """
    result = call_ops2deb("generate", configuration=configuration)
    assert result.exit_code == 0


def test_ops2deb_generate_should_generate_blueprints_from_multiple_configuration_files(
    tmp_path, call_ops2deb
):
    configuration_1 = """\
    - name: great-app
      version: 1.0.0
      summary: great package
      fetch: http://testserver/{{version}}/great-app.tar.gz
      install:
      - great-app:/usr/bin/great-app
    """

    configuration_2 = """\
    - name: super-app
      version: 1.0.0
      summary: super package
      fetch: http://testserver/{{version}}/super-app
      install:
      - super-app:/usr/bin/super-app
    """

    result = call_ops2deb("generate", configurations=[configuration_1, configuration_2])
    assert result.exit_code == 0
    assert (tmp_path / "great-app_1.0.0_amd64/src/usr/bin/great-app").is_file()
    assert (tmp_path / "great-app_1.0.0_amd64/debian/control").is_file()
    assert (tmp_path / "super-app_1.0.0_amd64/src/usr/bin/super-app").is_file()
    assert (tmp_path / "super-app_1.0.0_amd64/debian/control").is_file()


def test_ops2deb_generate_should_fail_gracefully_when_file_is_not_locked_in_lockfile_referenced_by_configuration_file(  # noqa: E501
    tmp_path,
    call_ops2deb,
):
    configuration_1 = """\
    name: great-app
    version: 1.0.0
    summary: great package
    fetch: http://testserver/{{version}}/great-app.tar.gz
    """

    configuration_2 = """\
    # lockfile=ops2deb-1.lock.yml
    name: super-app
    version: 1.0.0
    summary: super package
    fetch: http://testserver/{{version}}/super-app
    """

    result = call_ops2deb("generate", configurations=[configuration_1, configuration_2])
    assert result.exit_code == 77
    assert "Unknown hash for url http://testserver/1.0.0/super-app" in result.stdout


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


def test_ops2deb_update_should_update_all_matched_configuration_files(
    call_ops2deb, configuration_paths
):
    configuration_1 = """\
    name: great-app
    version: 1.0.0
    summary: great package
    fetch: http://testserver/{{version}}/great-app.tar.gz
    """

    configuration_2 = """\
    name: super-app
    version: 1.0.0
    summary: super package
    fetch: http://testserver/{{version}}/super-app
    """

    result = call_ops2deb("update", configurations=[configuration_1, configuration_2])
    raw_blueprints_0 = load_configuration_file(configuration_paths[0]).raw_blueprints
    assert result.exit_code == 0
    assert "great-app can be bumped from 1.0.0 to 1.1.1" in result.stdout
    assert raw_blueprints_0[0]["version"] == "1.1.1"
    raw_blueprints_1 = load_configuration_file(configuration_paths[1]).raw_blueprints
    assert "super-app can be bumped from 1.0.0 to 1.1.1" in result.stdout
    assert raw_blueprints_1[0]["version"] == "1.1.1"


def test_ops2deb_update_should_lock_new_urls_when_configuration_file_share_the_same_lockfile(  # noqa: E501
    call_ops2deb, configuration_paths, lockfile_path
):
    configuration_1 = """\
    name: great-app
    version: 1.0.0
    summary: great package
    fetch: http://testserver/{{version}}/great-app.tar.gz
    """

    configuration_2 = """\
    name: super-app
    version: 1.0.0
    summary: super package
    fetch: http://testserver/{{version}}/super-app
    """

    result = call_ops2deb("update", configurations=[configuration_1, configuration_2])
    assert result.exit_code == 0
    lock = Lock(lockfile_path)
    sha256 = "f1be6dd36b503641d633765655e81cdae1ff8f7f73a2582b7468adceb5e212a9"
    assert lock.sha256("http://testserver/1.1.1/great-app.tar.gz") == sha256
    sha256 = "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"
    assert lock.sha256("http://testserver/1.1.1/super-app") == sha256


def test_ops2deb_update_should_add_new_url_to_lock_file_referenced_by_configuration_file(
    call_ops2deb, configuration_paths, lockfile_paths
):
    configuration_1 = """\
    # lockfile=ops2deb-0.lock.yml
    name: great-app
    version: 1.0.0
    summary: great package
    fetch: http://testserver/{{version}}/great-app.tar.gz
    """

    configuration_2 = """\
    # lockfile=ops2deb-1.lock.yml
    name: super-app
    version: 1.0.0
    summary: super package
    fetch: http://testserver/{{version}}/super-app
    """

    result = call_ops2deb("update", configurations=[configuration_1, configuration_2])
    assert result.exit_code == 0
    lock_0 = Lock(lockfile_paths[0])
    sha256 = "f1be6dd36b503641d633765655e81cdae1ff8f7f73a2582b7468adceb5e212a9"
    assert lock_0.sha256("http://testserver/1.1.1/great-app.tar.gz") == sha256
    lock_1 = Lock(lockfile_paths[1])
    sha256 = "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"
    assert lock_1.sha256("http://testserver/1.1.1/super-app") == sha256


def test_ops2deb_update_should_add_new_url_to_both_lock_files_when_two_configuration_files_fetch_the_same_archive(  # noqa: E501
    call_ops2deb, configuration_paths, lockfile_paths
):
    configuration_1 = """\
    # lockfile=ops2deb-0.lock.yml
    name: great-app-1
    version: 1.0.0
    summary: great package
    fetch: http://testserver/{{version}}/great-app.tar.gz
    """

    configuration_2 = """\
    # lockfile=ops2deb-1.lock.yml
    name: great-app-2
    version: 1.0.0
    summary: great package
    fetch: http://testserver/{{version}}/great-app.tar.gz
    """

    result = call_ops2deb("update", configurations=[configuration_1, configuration_2])
    lock_0 = Lock(lockfile_paths[0])
    lock_1 = Lock(lockfile_paths[1])
    assert result.exit_code == 0
    sha256 = "f1be6dd36b503641d633765655e81cdae1ff8f7f73a2582b7468adceb5e212a9"
    assert lock_0.sha256("http://testserver/1.1.1/great-app.tar.gz") == sha256
    assert lock_1.sha256("http://testserver/1.1.1/great-app.tar.gz") == sha256


def test_ops2deb_update_should_succeed_with_valid_configuration(
    configuration_path, lockfile_path, call_ops2deb
):
    result = call_ops2deb("update")
    raw_blueprints = load_configuration_file(configuration_path).raw_blueprints
    lock = Lock(lockfile_path)
    sha256 = "f1be6dd36b503641d633765655e81cdae1ff8f7f73a2582b7468adceb5e212a9"
    assert "great-app can be bumped from 1.0.0 to 1.1.1" in result.stdout
    assert result.exit_code == 0
    assert raw_blueprints[1]["version"] == "1.1.1"
    assert lock.sha256("http://testserver/1.1.1/great-app.tar.gz") == sha256
    assert "http://testserver/1.0.0/great-app.tar.gz" not in lockfile_path.read_text()


def test_ops2deb_update_should_append_new_version_to_matrix_when_max_versions_is_not_reached(  # noqa: E501
    configuration_path, lockfile_path, call_ops2deb, summary_path
):
    result = call_ops2deb(
        "update",
        "--max-versions",
        "4",
        "--output-file",
        str(summary_path),
        configuration=mock_configuration_with_version_matrix,
    )
    raw_blueprints = load_configuration_file(configuration_path).raw_blueprints
    lock = Lock(lockfile_path)
    sha256 = "f1be6dd36b503641d633765655e81cdae1ff8f7f73a2582b7468adceb5e212a9"
    assert "Added great-app v1.1.1" in summary_path.read_text()
    assert "great-app can be bumped from 1.1.0 to 1.1.1" in result.stdout
    assert result.exit_code == 0
    assert raw_blueprints[0]["matrix"]["versions"] == ["1.0.0", "1.0.1", "1.1.0", "1.1.1"]
    assert lock.sha256("http://testserver/1.1.1/great-app.tar.gz") == sha256


def test_ops2deb_update_should_add_new_version_and_remove_old_versions_when_max_versions_is_reached(  # noqa: E501
    call_ops2deb, configuration_path, lockfile_path, summary_path
):
    result = call_ops2deb(
        "update",
        "--max-versions",
        "2",
        "--output-file",
        str(summary_path),
        configuration=mock_configuration_with_version_matrix,
    )
    raw_blueprints = load_configuration_file(configuration_path).raw_blueprints
    assert result.exit_code == 0
    assert raw_blueprints[0]["matrix"]["versions"] == ["1.1.0", "1.1.1"]
    assert "Added great-app v1.1.1 and removed v1.0.0, v1.0.1" in summary_path.read_text()
    assert "http://testserver/1.0.0/great-app.tar.gz" not in lockfile_path.read_text()
    assert "http://testserver/1.0.1/great-app.tar.gz" not in lockfile_path.read_text()
    assert "http://testserver/1.1.0/great-app.tar.gz" in lockfile_path.read_text()
    assert "http://testserver/1.1.1/great-app.tar.gz" in lockfile_path.read_text()


def test_ops2deb_update_should_replace_version_with_versions_matrix_when_max_versions_is_superior_to_one(  # noqa: E501
    call_ops2deb, configuration_path, lockfile_path, summary_path
):
    result = call_ops2deb(
        "update",
        "--max-versions",
        "2",
        "--output-file",
        str(summary_path),
    )
    raw_blueprints = load_configuration_file(configuration_path).raw_blueprints
    assert result.exit_code == 0
    assert raw_blueprints[1]["matrix"]["versions"] == ["1.0.0", "1.1.1"]
    assert "Added great-app v1.1.1" in summary_path.read_text()
    assert "http://testserver/1.0.0/great-app.tar.gz" in lockfile_path.read_text()
    assert "http://testserver/1.1.1/great-app.tar.gz" in lockfile_path.read_text()


def test_ops2deb_update_should_create_summary_when_called_with_output_file(
    call_ops2deb, summary_path
):
    call_ops2deb(
        "update",
        "--output-file",
        str(summary_path),
        configuration=mock_valid_configuration,
    )
    summary = (
        "Updated great-app from 1.0.0 to 1.1.1\n"
        "Updated super-app from 1.0.0 to 1.1.1\n"
    )
    assert summary_path.read_text() == summary


def test_ops2deb_update_should_create_empty_summary_when_called_with_output_file_and_config_is_up_to_date(  # noqa: E501
    call_ops2deb, summary_path
):
    call_ops2deb(
        "update",
        "--output-file",
        str(summary_path),
        configuration=mock_up_to_date_configuration,
    )
    assert summary_path.read_text() == ""


def test_ops2deb_update_should_succeed_with_single_blueprint_configuration(
    configuration_path, call_ops2deb
):
    result = call_ops2deb(
        "update", configuration=mock_configuration_single_blueprint_with_fetch
    )
    raw_blueprints = load_configuration_file(configuration_path).raw_blueprints
    assert result.exit_code == 0
    assert raw_blueprints[0]["version"] == "1.1.1"


def test_ops2deb_update_should_reset_blueprint_revision_to_one(
    configuration_path, call_ops2deb
):
    configuration = """
    - name: great-app
      version: 1.0.0
      revision: 2
      summary: Great package
      fetch: http://testserver/{{version}}/great-app.tar.gz
    """
    call_ops2deb("update", configuration=configuration)
    raw_blueprints = load_configuration_file(configuration_path).raw_blueprints
    assert "revision" not in raw_blueprints[0].keys()


def test_ops2deb_update_should_fail_gracefully_when_server_error(
    call_ops2deb, configuration_path
):
    configuration_with_server_error = """\
    - name: bad-app
      version: 1.0.0
      summary: Bad package
      fetch: http://testserver/{{version}}/500.zip

    - name: great-app
      version: 1.0.0
      summary: Great package
      fetch: http://testserver/{{version}}/great-app.tar.gz
    """

    result = call_ops2deb("update", configuration=configuration_with_server_error)
    raw_blueprints = load_configuration_file(configuration_path).raw_blueprints
    error = "Server error when requesting http://testserver/1.1.0/500.zip"
    assert error in result.stdout
    assert raw_blueprints[1]["version"] == "1.1.1"
    assert result.exit_code == 77


def test_ops2deb_update_should_fail_gracefully_with_multiarch_blueprint_when_404_error_on_a_file(  # noqa E501
    call_ops2deb,
):
    result = call_ops2deb(
        "update",
        configuration=mock_configuration_with_multi_arch_remote_file_and_404_on_one_file,
    )
    error = "Failed to download http://testserver/1.1.1/great-app-404.tar.gz."
    assert error in result.stdout
    assert result.exit_code == 77


def test_ops2deb_update_should_skip_blueprints_when_skip_option_is_used(
    configuration_path, call_ops2deb
):
    result = call_ops2deb("update", "--skip", "great-app", "-s", "super-app")
    assert result.exit_code == 0
    assert configuration_path.read_text() == mock_valid_configuration


def test_ops2deb_update_should_only_update_blueprints_listed_with_only_option(
    configuration_path, call_ops2deb
):
    result = call_ops2deb("update", "--only", "great-app")
    raw_blueprints = load_configuration_file(configuration_path).raw_blueprints
    assert result.exit_code == 0
    assert raw_blueprints[1]["version"] == "1.1.1"
    assert raw_blueprints[2]["version"] == "1.0.0"


@pytest.mark.parametrize(
    "configuration", [mock_configuration_with_version_matrix, mock_valid_configuration]
)
def test_ops2deb_update_config_should_not_need_formatting_when_formatted_before_update(
    configuration, configuration_path, call_ops2deb
):
    result = call_ops2deb("update", configuration=configuration)
    assert result.exit_code == 0
    result = call_ops2deb("format")
    assert result.exit_code == 0


def test_ops2deb_format_should_be_idempotent(call_ops2deb, configuration_paths):
    configurations = [
        mock_configuration_not_properly_formatted,
        mock_configuration_with_version_matrix,
        mock_configuration_with_multi_arch_remote_file,
        mock_configuration_single_blueprint_with_fetch,
    ]
    call_ops2deb("format", configurations=configurations)
    formatted_configurations = [path.read_text() for path in configuration_paths[:4]]
    call_ops2deb("format", configurations=configurations, write=False)
    reformatted_configurations = [path.read_text() for path in configuration_paths[:4]]
    assert formatted_configurations[0] == reformatted_configurations[0]
    assert formatted_configurations[1] == reformatted_configurations[1]
    assert formatted_configurations[2] == reformatted_configurations[2]
    assert formatted_configurations[3] == reformatted_configurations[3]


def test_ops2deb_format_should_not_modify_already_formatted_configuration(
    call_ops2deb, configuration_path
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


def test_ops2deb_format_should_not_remove_lockfile_path_comment_when_its_not_the_default(
    call_ops2deb, configuration_path
):
    result = call_ops2deb("format", configuration=mock_configuration_with_lockfile_path)
    assert result.exit_code == 77
    assert "# lockfile=great-app.lock.yml" in configuration_path.read_text()


def test_ops2deb_lock_should_succeed_when_configuration_file_is_valid(call_ops2deb):
    result = call_ops2deb("lock")
    assert result.exit_code == 0


def test_ops2deb_lock_should_succeed_with_valid_multi_arch_fetch(call_ops2deb):
    result = call_ops2deb(
        "lock", configuration=mock_configuration_with_multi_arch_remote_file
    )
    assert result.exit_code == 0


def test_ops2deb_lock_should_only_download_files_that_are_not_locked(
    call_ops2deb, cache_path
):
    configuration = """\
    - name: great-app
      version: 1.1.1
      summary: this file is not locked
      fetch: http://testserver/{{version}}/great-app.tar.gz

    - name: super-app
      version: 1.0.0
      architecture: all
      summary: this one is
      fetch: http://testserver/{{version}}/super-app
    """
    call_ops2deb("lock", configuration=configuration)
    fetched = {file.name for file in (cache_path / "").glob("**/*") if file.is_file()}
    assert fetched == {"great-app.tar.gz", "great-app.tar.gz.sum"}


def test_ops2deb_lock_should_add_missing_urls_in_lockfile_referenced_by_configuration_file(  # noqa: E501
    call_ops2deb, cache_path, lockfile_paths
):
    configuration_0 = """\
    # lockfile=ops2deb-0.lock.yml
    name: great-app
    version: 1.1.1
    summary: this file is not locked
    fetch: http://testserver/{{version}}/great-app.tar.gz
    """

    configuration_1 = """\
    # lockfile=ops2deb-1.lock.yml
    name: super-app
    version: 1.0.0
    architecture: all
    summary: this one is
    fetch: http://testserver/{{version}}/super-app
    """

    result = call_ops2deb("lock", configurations=[configuration_0, configuration_1])
    assert result.exit_code == 0

    lockfile_0 = lockfile_paths[0].read_text()
    assert "http://testserver/1.1.1/great-app.tar.gz" in lockfile_0
    assert "http://testserver/1.0.0/super-app" not in lockfile_0

    lockfile_1 = lockfile_paths[1].read_text()
    assert "http://testserver/1.0.0/super-app" in lockfile_1
    assert "http://testserver/1.1.1/great-app.tar.gz" not in lockfile_1


@pytest.mark.parametrize("subcommand", ["default", "update", "generate", "lock"])
def test_ops2deb_should_support_lock_file_header_in_configuration_file(
    call_ops2deb, subcommand, tmp_path, lockfile_path, tmp_working_directory
):
    shutil.move(lockfile_path, "great-app.lock.yml")
    result = call_ops2deb(subcommand, configuration=mock_configuration_with_lockfile_path)
    assert result.exit_code == 0
    assert lockfile_path.is_file() is False


@pytest.mark.parametrize(
    "subcommand", ["update", "generate", "format", "validate", "lock"]
)
def test_ops2deb_should_exit_with_error_code_when_configuration_file_has_invalid_yaml(
    call_ops2deb, subcommand
):
    configuration_with_yaml_error = """\
    - name: awesome-metapackage
        version: 1.0.0
    """
    result = call_ops2deb(subcommand, configuration=configuration_with_yaml_error)
    assert "Failed to parse" in result.stdout
    assert result.exit_code == 77


@pytest.mark.parametrize(
    "subcommand", ["update", "generate", "format", "validate", "lock"]
)
def test_ops2deb_should_exit_with_error_code_when_configuration_file_has_validation_error(
    call_ops2deb, subcommand, configuration_path
):
    configuration = """\
    - name: awesome-metapackage
    """
    result = call_ops2deb(subcommand, configuration=configuration)
    assert f"ailed to parse blueprint at index 0 in {configuration_path}" in result.stdout
    assert result.exit_code == 77
