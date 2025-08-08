import json
import os
from textwrap import dedent

import pytest
import ruamel.yaml
from typer.testing import CliRunner

from ops2deb.cli import app
from ops2deb.lockfile import LockFile
from ops2deb.parser import load_configuration_file

yaml = ruamel.yaml.YAML(typ="safe")

configuration_example_0 = """\
name: great-app
matrix:
  architectures:
    - amd64
    - armhf
version: 1.0.0
summary: great package
description: A detailed description of the great package.
fetch: http://testserver/{{version}}/great-app-{{arch}}.tar.gz
install:
  - content: 2021 John Doe. All rights reserved.
    path: debian/copyright
  - great-app:/usr/bin/great-app
"""

configuration_example_1 = """\
- name: awesome-metapackage
  version: "{{env('CI_COMMIT_TAG', '1.0.0')}}"
  epoch: 1
  architecture: all
  summary: Awesome metapackage
  description: A detailed description of the awesome metapackage.
  depends:
    - great-app

- name: super-app
  version: 1.0.0
  architecture: all
  summary: super package
  description: |-
    A detailed description of the super package
    Lorem ipsum dolor sit amet, consectetur adipiscing elit
  fetch: http://testserver/{{version}}/super-app
  script:
    - mv super-app {{src}}/usr/bin/super-app
"""


@pytest.fixture
def call_ops2deb(
    tmp_path,
    configuration_paths,
    lockfile_path,
    cache_path,
    mock_httpx_client,
    lockfile_content,
):
    def _invoke(
        *args,
        configurations: list[str],
    ):
        runner = CliRunner()
        for index, configuration in enumerate(configurations):
            configuration_paths[index].write_text(dedent(configuration))
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


def test_purge__deletes_files_in_cache_path(call_ops2deb, cache_path):
    # Given
    (cache_path / "test").write_text("test")

    # When
    result = call_ops2deb("purge", configurations=[])

    # Then
    assert result.exit_code == 0
    assert (cache_path / "test").exists() is False


def test_generate__converts_blueprints_to_debian_source_packages(
    call_ops2deb, configuration_paths, tmp_path
):
    # Given
    configurations = [configuration_example_0, configuration_example_1]

    # When
    result = call_ops2deb("generate", configurations=configurations)

    # Then
    assert result.exit_code == 0
    assert (tmp_path / "great-app_1.0.0_amd64/src/usr/bin/great-app").is_file()
    assert (tmp_path / "great-app_1.0.0_amd64/debian/control").is_file()
    assert (tmp_path / "great-app_1.0.0_amd64/debian/install").is_file()
    assert (tmp_path / "great-app_1.0.0_amd64/debian/copyright").is_file()
    assert (tmp_path / "great-app_1.0.0_amd64/debian/changelog").is_file()
    assert (tmp_path / "great-app_1.0.0_armhf/src/usr/bin/great-app").is_file()
    assert (tmp_path / "great-app_1.0.0_armhf/debian/control").is_file()
    assert (tmp_path / "great-app_1.0.0_armhf/debian/install").is_file()
    assert (tmp_path / "great-app_1.0.0_armhf/debian/copyright").is_file()
    assert (tmp_path / "great-app_1.0.0_armhf/debian/changelog").is_file()
    assert (tmp_path / "super-app_1.0.0_all/src/usr/bin/super-app").is_file()
    assert (tmp_path / "super-app_1.0.0_all/debian/control").is_file()
    assert (tmp_path / "super-app_1.0.0_all/debian/install").is_file()
    assert (tmp_path / "super-app_1.0.0_all/debian/changelog").is_file()
    assert (tmp_path / "awesome-metapackage_1.0.0_all/debian/control").is_file()
    assert (tmp_path / "awesome-metapackage_1.0.0_all/debian/rules").is_file()


def test_generate__should_not_download_already_cached_archives(call_ops2deb):
    # Given
    configuration = """
    name: super-app
    version: 1.0.0
    summary: super package
    fetch: http://testserver/{{version}}/super-app
    """

    # When
    result_0 = call_ops2deb("generate", configurations=[configuration])
    result_1 = call_ops2deb("generate", configurations=[configuration])

    # Then
    assert "Downloading super-app..." in result_0.stdout
    assert "Downloading super-app..." not in result_1.stdout


def test_generate__should_fail_when_downloaded_file_checksum_does_not_match_lockfile(
    call_ops2deb,
):
    # Given
    configuration = """
    name: bad-app
    version: 1.0.0
    architecture: all
    summary: Bad package
    fetch: http://testserver/1.0.0/wrong_checksum-app.tar.gz
    """

    # When
    result = call_ops2deb("generate", configurations=[configuration])

    # Then
    assert result.exit_code == 77
    assert "Wrong checksum for file wrong_checksum-app.tar.gz" in result.stderr


def test_generate__should_fail_gracefully_when_server_returns_a_404(call_ops2deb):
    # Given
    configuration = """
    name: bad-app
    version: 1.0.0
    architecture: all
    summary: oops test server replies with 404
    fetch: http://testserver/{{version}}/404.zip
    """

    # When
    result = call_ops2deb("generate", configurations=[configuration])

    # Then
    expected_error = (
        "Failed to download http://testserver/1.0.0/404.zip. Server responded with 404."
    )
    assert expected_error in result.stderr
    assert result.exit_code == 77


def test_generate__should_not_generate_packages_already_published_in_debian_repo_when_repository_option_is_used(  # noqa: E501
    call_ops2deb, tmp_path
):
    # Given
    configuration_0 = """
    name: great-app
    version: 1.1.0
    summary: great package
    fetch: http://testserver/{{version}}/great-app.tar.gz
    """

    configuration_1 = """
    name: super-app
    version: 1.0.0
    architecture: all
    summary: super package
    fetch: http://testserver/{{version}}/super-app
    """

    # When
    result = call_ops2deb(
        "generate",
        "-r",
        "http://deb.wakemeops.com stable",
        configurations=[configuration_0, configuration_1],
    )

    # Then
    assert result.exit_code == 0
    assert (tmp_path / "great-app_1.1.0_amd64/debian/control").is_file()
    assert (tmp_path / "super-app_1.0.0_all/debian/control").is_file() is False


def test_generate__should_run_script_from_config_directory_when_blueprint_has_not_fetch_instruction(  # noqa: E501
    call_ops2deb, tmp_path
):
    # Given
    configuration = """\
    name: cool-app
    version: 1.0.0
    architecture: all
    summary: Cool package
    description: |
      A detailed description of the cool package
    install:
      - cool-app.sh:/usr/bin/cool-app
    """
    (tmp_path / "cool-app.sh").touch()

    # When
    result = call_ops2deb("generate", configurations=[configuration])

    # Then
    assert result.exit_code == 0
    assert (tmp_path / "cool-app_1.0.0_all/src/usr/bin/cool-app").is_file()


def test_generate__should_honor_only_argument(call_ops2deb, tmp_path):
    # Given
    configuration_0 = """
    - name: cool-app-0
      version: 1.0.0
      summary: cool package

    - name: cool-app-1
      version: 1.0.0
      summary: cool package
    """

    configuration_1 = """
    name: cool-app-2
    version: 1.0.0
    summary: Cool package
    """

    # When
    result = call_ops2deb(
        "generate",
        "--only",
        "cool-app-0",
        configurations=[configuration_0, configuration_1],
    )

    # Then
    assert result.exit_code == 0
    assert list(tmp_path.glob("*_amd64")) == [tmp_path / "cool-app-0_1.0.0_amd64"]


def test_generate__should_not_crash_when_archive_contains_a_dangling_symlink(
    call_ops2deb, tmp_path
):
    # Given
    configuration = """
    - name: great-app
      summary: Great package
      version: 1.0.0
      description: A detailed description of the great package.
      fetch: http://testserver/{{version}}/dangling-symlink.tar.xz
    """

    # When
    result = call_ops2deb("generate", configurations=[configuration])

    # Then
    assert result.exit_code == 0


def test_generate__cwd_variable_points_to_config_directory_when_blueprint_has_a_fetch_and_path_to_config_is_relative(  # noqa: E501
    call_ops2deb, tmp_working_directory, configuration_path, tmp_path
):
    # Given
    configuration = """\
    name: great-app
    version: 1.0.0
    summary: great package
    fetch: http://testserver/{{version}}/great-app.tar.gz
    script:
      - mv great-app {{src}}/usr/bin/great-app
      - cp {{cwd}}/test.conf {{src}}/etc/test.conf
    """
    (tmp_path / "test.conf").touch()

    # When
    result = call_ops2deb(
        "generate", "-c", configuration_path.name, configurations=[configuration]
    )

    # Then
    assert result.exit_code == 0


def test_generate__runs_pre_script_before_script(
    call_ops2deb, tmp_working_directory, tmp_path
):
    # Given
    configuration_0 = """\
    - name: great-app-3
      version: 1.0.0
      summary: great package
      pre_script:
      - touch {{cwd}}/great-app
      install:
      - great-app:/usr/bin/great-app
      script:
      - mv {{src}}/usr/bin/great-app {{src}}/usr/bin/lame-app
    """

    # When
    result = call_ops2deb("generate", configurations=[configuration_0])

    # Then
    assert result.exit_code == 0


def test_generate__should_not_crash_when_multiple_blueprints_have_fetch_set_to_the_same_url(  # noqa: E501
    call_ops2deb, tmp_working_directory, tmp_path
):
    # Given
    configuration_0 = """\
    - name: great-app-1
      version: 1.0.0
      summary: great package
      fetch: http://testserver/{{version}}/great-app.tar.gz
      install:
      - great-app:/usr/bin/great-app
    - name: great-app-2
      version: 1.0.0
      summary: great package
      fetch: http://testserver/{{version}}/great-app.tar.gz
      install:
      - great-app:/usr/bin/great-app
    """

    configuration_1 = """\
    - name: great-app-3
      version: 1.0.0
      summary: great package
      fetch: http://testserver/{{version}}/great-app.tar.gz
      install:
      - great-app:/usr/bin/great-app
    """

    # When
    result = call_ops2deb("generate", configurations=[configuration_0, configuration_1])

    # Then
    assert result.exit_code == 0


def test_generate__should_fail_gracefully_when_file_is_not_locked_in_lockfile_referenced_by_configuration_file(  # noqa: E501
    call_ops2deb, tmp_path
):
    # Given
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

    # When
    result = call_ops2deb("generate", configurations=[configuration_1, configuration_2])

    # Then
    assert result.exit_code == 77
    assert "Unknown hash for url http://testserver/1.0.0/super-app" in result.stderr


def test_build__builds_debian_source_packages(tmp_path, call_ops2deb):
    # Given
    configurations = [configuration_example_0, configuration_example_1]
    expected_package_set = {
        "great-app_1.0.0-1~ops2deb_armhf.deb",
        "super-app_1.0.0-1~ops2deb_all.deb",
        "great-app_1.0.0-1~ops2deb_amd64.deb",
        "awesome-metapackage_1.0.0-1~ops2deb_all.deb",
    }

    # When
    call_ops2deb("generate", configurations=configurations)
    result = call_ops2deb("build", configurations=configurations)

    # Then
    assert result.exit_code == 0
    assert set([path.name for path in tmp_path.glob("*.deb")]) == expected_package_set


def test_default__generates_and_builds_debian_source_packages(call_ops2deb, tmp_path):
    # Given
    configurations = [configuration_example_0, configuration_example_1]
    expected_package_set = {
        "great-app_1.0.0-1~ops2deb_armhf.deb",
        "super-app_1.0.0-1~ops2deb_all.deb",
        "great-app_1.0.0-1~ops2deb_amd64.deb",
        "awesome-metapackage_1.0.0-1~ops2deb_all.deb",
    }

    # When
    result = call_ops2deb("default", configurations=configurations)

    # Then
    assert result.exit_code == 0
    assert set([path.name for path in tmp_path.glob("*.deb")]) == expected_package_set


def test_build__exits_with_error_when_build_fails(call_ops2deb, tmp_path):
    # Given
    configuration = """\
    name: bad-app
    version: 1.0.0
    summary: great package
    script:
    - echo "invalid_control_file" > {{debian}}/control
    """

    # When
    result_generate = call_ops2deb("generate", configurations=[configuration])
    result_build = call_ops2deb("build", configurations=[configuration])

    # Then
    assert result_generate.exit_code == 0
    assert result_build.exit_code == 77


def test_update__updates_version_field_when_max_versions_is_set_to_default_value_one(
    call_ops2deb, configuration_paths
):
    # Given
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

    # When
    result = call_ops2deb("update", configurations=[configuration_1, configuration_2])

    # Then
    raw_blueprints_0 = load_configuration_file(configuration_paths[0]).raw_blueprints
    assert result.exit_code == 0
    assert "great-app can be bumped from 1.0.0 to 1.1.1" in result.stdout
    assert raw_blueprints_0[0]["version"] == "1.1.1"
    raw_blueprints_1 = load_configuration_file(configuration_paths[1]).raw_blueprints
    assert "super-app can be bumped from 1.0.0 to 1.1.1" in result.stdout
    assert raw_blueprints_1[0]["version"] == "1.1.1"


def test_update__adds_new_urls_to_lockfile_when_configuration_files_share_the_same_lockfile(  # noqa: E501
    call_ops2deb, configuration_paths, lockfile_path
):
    # Given
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

    # When
    result = call_ops2deb("update", configurations=[configuration_1, configuration_2])

    # Then
    assert result.exit_code == 0
    lock = LockFile(lockfile_path)
    sha256 = "f1be6dd36b503641d633765655e81cdae1ff8f7f73a2582b7468adceb5e212a9"
    assert lock.sha256("http://testserver/1.1.1/great-app.tar.gz") == sha256
    sha256 = "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"
    assert lock.sha256("http://testserver/1.1.1/super-app") == sha256


def test_update__adds_new_url_to_lock_file_referenced_by_configuration_file(
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
    lock_0 = LockFile(lockfile_paths[0])
    sha256 = "f1be6dd36b503641d633765655e81cdae1ff8f7f73a2582b7468adceb5e212a9"
    assert lock_0.sha256("http://testserver/1.1.1/great-app.tar.gz") == sha256
    lock_1 = LockFile(lockfile_paths[1])
    sha256 = "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"
    assert lock_1.sha256("http://testserver/1.1.1/super-app") == sha256


def test_update__adds_new_url_to_both_lock_files_when_two_configuration_files_with_different_lockfile_fetch_the_same_archive(  # noqa: E501
    call_ops2deb, configuration_paths, lockfile_paths
):
    # Given
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

    # When
    result = call_ops2deb("update", configurations=[configuration_1, configuration_2])

    # Then
    lock_0 = LockFile(lockfile_paths[0])
    lock_1 = LockFile(lockfile_paths[1])
    assert result.exit_code == 0
    sha256 = "f1be6dd36b503641d633765655e81cdae1ff8f7f73a2582b7468adceb5e212a9"
    assert lock_0.sha256("http://testserver/1.1.1/great-app.tar.gz") == sha256
    assert lock_1.sha256("http://testserver/1.1.1/great-app.tar.gz") == sha256


def test_update__appends_new_version_to_version_matrix_when_max_versions_is_not_reached(
    call_ops2deb, configuration_path, lockfile_path, summary_path
):
    # Given
    configuration = """
    - name: great-app
      matrix:
        versions:
        - 1.0.0
        - 1.0.1
        - 1.1.0
      summary: great package
      fetch: http://testserver/{{version}}/great-app.tar.gz
    """

    # When
    result = call_ops2deb(
        "update",
        "--max-versions",
        "4",
        "--output-file",
        str(summary_path),
        configurations=[configuration],
    )

    # Then
    raw_blueprints = load_configuration_file(configuration_path).raw_blueprints
    lock = LockFile(lockfile_path)
    sha256 = "f1be6dd36b503641d633765655e81cdae1ff8f7f73a2582b7468adceb5e212a9"
    assert "Add great-app v1.1.1" in summary_path.read_text()
    assert "great-app can be bumped from 1.1.0 to 1.1.1" in result.stdout
    assert raw_blueprints[0]["matrix"]["versions"] == ["1.0.0", "1.0.1", "1.1.0", "1.1.1"]
    assert lock.sha256("http://testserver/1.1.1/great-app.tar.gz") == sha256
    assert result.exit_code == 0


def test_update__adds_new_version_and_remove_old_versions_when_max_versions_is_reached(
    call_ops2deb, configuration_path, lockfile_path, summary_path
):
    # Given
    configuration = """
    - name: great-app
      matrix:
        versions:
        - 1.0.0
        - 1.0.1
        - 1.1.0
      summary: great package
      fetch: http://testserver/{{version}}/great-app.tar.gz
    """

    # When
    result = call_ops2deb(
        "update",
        "--max-versions",
        "2",
        "--output-file",
        str(summary_path),
        configurations=[configuration],
    )

    # Then
    raw_blueprints = load_configuration_file(configuration_path).raw_blueprints
    assert raw_blueprints[0]["matrix"]["versions"] == ["1.1.0", "1.1.1"]
    assert "Add great-app v1.1.1" in summary_path.read_text()
    assert "Remove great-app v1.0.0\nRemove great-app v1.0.1" in summary_path.read_text()
    assert "http://testserver/1.0.0/great-app.tar.gz" not in lockfile_path.read_text()
    assert "http://testserver/1.0.1/great-app.tar.gz" not in lockfile_path.read_text()
    assert "http://testserver/1.1.0/great-app.tar.gz" in lockfile_path.read_text()
    assert "http://testserver/1.1.1/great-app.tar.gz" in lockfile_path.read_text()
    assert result.exit_code == 0


def test_update__replaces_version_with_versions_matrix_when_max_versions_is_superior_to_one(  # noqa: E501
    call_ops2deb, configuration_path, lockfile_path, summary_path
):
    # Given
    configuration = """
    - name: great-app
      version: 1.0.0
      summary: great package
      fetch: http://testserver/{{version}}/great-app.tar.gz
    """

    # When
    result = call_ops2deb(
        "update",
        "--max-versions",
        "2",
        "--output-file",
        str(summary_path),
        configurations=[configuration],
    )

    # Then
    raw_blueprints = load_configuration_file(configuration_path).raw_blueprints
    assert result.exit_code == 0
    assert raw_blueprints[0]["matrix"]["versions"] == ["1.0.0", "1.1.1"]
    assert "Add great-app v1.1.1" in summary_path.read_text()
    assert "http://testserver/1.0.0/great-app.tar.gz" in lockfile_path.read_text()
    assert "http://testserver/1.1.1/great-app.tar.gz" in lockfile_path.read_text()


def test_update__creates_a_summary_of_updated_blueprints_when_called_with_output_file_argument(  # noqa: E501
    call_ops2deb, summary_path
):
    # Given
    configuration_0 = """\
    name: great-app
    version: 1.0.0
    summary: great package
    fetch: http://testserver/{{version}}/great-app.tar.gz
    """

    configuration_1 = """\
    name: super-app
    version: 1.0.0
    summary: super package
    fetch: http://testserver/{{version}}/super-app
    """

    # When
    call_ops2deb(
        "update",
        "--output-file",
        str(summary_path),
        configurations=[configuration_0, configuration_1],
    )

    # Then
    summary_lines_set = {
        "",
        "Update great-app from v1.0.0 to v1.1.1",
        "Update super-app from v1.0.0 to v1.1.1",
    }
    assert set(summary_path.read_text().split("\n")) == summary_lines_set


def test_update__creates_empty_summary_when_called_with_output_file_and_configuration_is_up_to_date(  # noqa: E501
    call_ops2deb, summary_path
):
    # Given
    configuration = """
    - name: great-app
      version: 1.1.1
      revision: 2
      architecture: all
      summary: great package
      description: A detailed description of the great package.
      fetch: http://testserver/{{version}}/great-app.tar.gz
    """

    # When
    call_ops2deb(
        "update",
        "--output-file",
        str(summary_path),
        configurations=[configuration],
    )

    # Then
    assert summary_path.read_text() == ""


def test_update__resets_blueprint_revision_to_one_when_a_new_release_is_available(
    call_ops2deb, configuration_path
):
    # Given
    configuration = """
    name: great-app
    version: 1.0.0
    revision: 2
    summary: Great package
    fetch: http://testserver/{{version}}/great-app.tar.gz
    """

    # When
    call_ops2deb("update", configurations=[configuration])

    # Then
    raw_blueprints = load_configuration_file(configuration_path).raw_blueprints
    assert "revision" not in raw_blueprints[0].keys()


def test_update__doesnt_stop_when_server_replies_with_a_500_for_one_url(
    call_ops2deb, configuration_path
):
    # Given
    configuration = """\
    - name: bad-app
      version: 1.0.0
      summary: oops, server replies with a 500
      fetch: http://testserver/{{version}}/500.zip

    - name: great-app
      version: 1.0.0
      summary: will still be updated
      fetch: http://testserver/{{version}}/great-app.tar.gz
    """

    # When
    result = call_ops2deb("update", configurations=[configuration])

    # Then
    raw_blueprints = load_configuration_file(configuration_path).raw_blueprints
    expected_error = "Server error when requesting http://testserver/1.1.0/500.zip"
    assert expected_error in result.stderr
    assert raw_blueprints[1]["version"] == "1.1.1"
    assert result.exit_code == 77


def test_update__doesnt_update_blueprint_when_fetch_fails_for_two_architectures(
    call_ops2deb, configuration_path
):
    # Given
    configuration = """
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
          armhf: "404"
          arm64: "404"
      script:
      - mv great-app {{src}}/usr/bin/great-app
    """

    # When
    result = call_ops2deb("update", configurations=[configuration])

    # Then
    expected_error = "Failed to download http://testserver/1.1.1/great-app-404.tar.gz."
    assert expected_error in result.stderr
    assert configuration_path.read_text() == dedent(configuration)
    assert result.exit_code == 77


def test_update__skips_blueprints_when_skip_option_is_used(
    call_ops2deb, configuration_paths
):
    # Given
    configuration_0 = """\
    name: great-app
    version: 1.0.0
    summary: great package
    fetch: http://testserver/{{version}}/great-app.tar.gz
    """

    configuration_1 = """\
    name: super-app
    version: 1.0.0
    summary: super package
    fetch: http://testserver/{{version}}/super-app
    """

    # When
    result = call_ops2deb(
        "update",
        "--skip",
        "great-app",
        "-s",
        "super-app",
        configurations=[configuration_0, configuration_1],
    )

    # Then
    assert result.exit_code == 0
    assert configuration_paths[0].read_text() == dedent(configuration_0)
    assert configuration_paths[1].read_text() == dedent(configuration_1)


def test_update___updates_only_blueprints_listed_with_only_option(
    call_ops2deb, configuration_paths
):
    # Given
    configuration_0 = """\
    name: great-app
    version: 1.0.0
    summary: great package
    fetch: http://testserver/{{version}}/great-app.tar.gz
    """

    configuration_1 = """\
    name: super-app
    version: 1.0.0
    summary: super package
    fetch: http://testserver/{{version}}/super-app
    """

    # When
    result = call_ops2deb(
        "update", "--only", "great-app", configurations=[configuration_0, configuration_1]
    )

    # Then
    raw_blueprints_0 = load_configuration_file(configuration_paths[0]).raw_blueprints
    raw_blueprints_1 = load_configuration_file(configuration_paths[1]).raw_blueprints
    assert result.exit_code == 0
    assert raw_blueprints_0[0]["version"] == "1.1.1"
    assert raw_blueprints_1[0]["version"] == "1.0.0"


def test_update__should_not_produce_configuration_files_that_dont_pass_format_command(
    call_ops2deb, configuration_paths
):
    # Given
    configuration = """\
    name: great-app
    matrix:
      versions:
        - 1.0.0
        - 1.0.1
        - 1.1.0
    summary: great package
    fetch: http://testserver/{{version}}/great-app.tar.gz
    script:
      - mv great-app {{src}}/usr/bin/great-app
    """

    # When
    result_update = call_ops2deb(
        "update",
        configurations=[configuration, configuration_example_0, configuration_example_1],
    )
    result_format = call_ops2deb("format", configurations=[])

    # Then
    assert result_update.exit_code == 0
    assert result_format.exit_code == 0


def test_update___only_looks_for_updates_for_the_last_blueprint_when_multiple_blueprints_have_the_same_name(  # noqa: E501
    call_ops2deb, configuration_paths
):
    # Given
    configuration = """\
    - name: great-app
      version: 1.0.0
      summary: great package
      fetch: http://testserver/{{version}}/great-app.tar.gz

    - name: great-app
      version: 1.0.1
      summary: super package
      fetch: http://testserver/{{version}}/great-app.tar.gz
    """

    # When
    result = call_ops2deb("update", "--only", "great-app", configurations=[configuration])

    # Then
    raw_blueprints = load_configuration_file(configuration_paths[0]).raw_blueprints
    assert result.exit_code == 0
    assert raw_blueprints[0]["version"] == "1.0.0"
    assert raw_blueprints[1]["version"] == "1.1.1"


def test__format_should_be_idempotent(call_ops2deb, configuration_paths):
    # Given
    configuration_0 = """
    - name: great-app-0
      summary: great package
      version: 1.0.0
      architecture: all
      description: |
        A detailed description of the great package.
      fetch: http://testserver/{{version}}/great-app.tar.gz
      revision: 2
      script:
      - mv great-app {{src}}/usr/bin/great-app
    """

    configuration_1 = """
    - name: great-app-1
      matrix:
        versions: ["1.0.0"]
        architectures:
        - amd64
        - armhf
      summary: great package
      version: 1.0.0
      architecture: amd64
      description: |
        A detailed description of the great package.
      revision: 1
      script:
      - mv great-app {{src}}/usr/bin/great-app
    """

    configurations = [
        configuration_example_0,
        configuration_example_1,
        configuration_0,
        configuration_1,
    ]

    # When
    call_ops2deb("format", configurations=configurations)
    formatted_configurations = [path.read_text() for path in configuration_paths[:4]]
    call_ops2deb("format", configurations=configurations)
    reformatted_configurations = [path.read_text() for path in configuration_paths[:4]]

    # Then
    assert formatted_configurations[0] == reformatted_configurations[0]
    assert formatted_configurations[1] == reformatted_configurations[1]
    assert formatted_configurations[2] == reformatted_configurations[2]
    assert formatted_configurations[3] == reformatted_configurations[3]


def test_format__does_not_modify_already_formatted_configuration(
    call_ops2deb, configuration_paths
):
    # Given
    configurations = [configuration_example_0, configuration_example_1]

    # When
    result = call_ops2deb("format", configurations=configurations)

    # Then
    assert configuration_paths[0].read_text() == configuration_example_0
    assert configuration_paths[1].read_text() == configuration_example_1
    assert result.exit_code == 0


def test_format__formats_and_exits_with_error_code_when_file_is_not_properly_formatted(
    call_ops2deb, configuration_path
):
    # Given
    configuration = """
    - name: great-app
      summary: great package
      revision: 2
      matrix:
        versions: ["1.0.0"]
      architecture: all
      description: |
        A detailed description of the great package.
      fetch: http://testserver/{{version}}/great-app.tar.gz
      script:
      - mv great-app {{src}}/usr/bin/great-app
    """

    expected_formatting = """\
    name: great-app
    matrix:
      versions:
        - 1.0.0
    revision: "2"
    architecture: all
    summary: great package
    description: A detailed description of the great package.
    fetch: http://testserver/{{version}}/great-app.tar.gz
    script:
      - mv great-app {{src}}/usr/bin/great-app
    """

    # When
    result = call_ops2deb("format", configurations=[configuration])

    # Then
    assert result.exit_code == 77
    assert configuration_path.read_text() == dedent(expected_formatting)


def test_format__does_not_remove_lockfile_path_comment_when_its_not_the_default(
    call_ops2deb, configuration_path
):
    # Given
    configuration = """\
    # lockfile=great-app.lock.yml
    name: great-app
    version: 1.0.0
    summary: this is a summary
    fetch: http://testserver/{{version}}/great-app.tar.gz
    """

    # When
    result = call_ops2deb("format", configurations=[configuration])

    # Then
    assert "# lockfile=great-app.lock.yml" in configuration_path.read_text()
    assert result.exit_code == 77


def test_lock__downloads_only_files_that_are_not_yet_locked(
    call_ops2deb, cache_path, lockfile_paths
):
    # Given
    configuration_0 = """
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

    configuration_1 = """\
    # lockfile=ops2deb-1.lock.yml
    - name: great-app
      matrix:
        architectures:
        - amd64
        - armhf
      version: 1.0.0
      summary: Great package
      description: A detailed description of the great package.
      fetch: http://testserver/{{version}}/great-app-{{arch}}.tar.gz
    """

    expected_files_in_cache = {
        "great-app-amd64.tar.gz",
        "great-app-amd64.tar.gz.sum",
        "great-app-armhf.tar.gz",
        "great-app-armhf.tar.gz.sum",
        "great-app.tar.gz",
        "great-app.tar.gz.sum",
    }

    # When
    result = call_ops2deb("lock", configurations=[configuration_0, configuration_1])

    # Then
    fetched = {file.name for file in cache_path.glob("**/*") if file.is_file()}
    assert fetched == expected_files_in_cache
    assert result.exit_code == 0


def test_lock__adds_missing_urls_in_lockfile_referenced_by_configuration_file(
    call_ops2deb, cache_path, lockfile_paths
):
    # Given
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

    # When
    result = call_ops2deb("lock", configurations=[configuration_0, configuration_1])

    # Then
    assert result.exit_code == 0
    lockfile_0 = lockfile_paths[0].read_text()
    assert "http://testserver/1.1.1/great-app.tar.gz" in lockfile_0
    assert "http://testserver/1.0.0/super-app" not in lockfile_0
    lockfile_1 = lockfile_paths[1].read_text()
    assert "http://testserver/1.0.0/super-app" in lockfile_1
    assert "http://testserver/1.1.1/great-app.tar.gz" not in lockfile_1


def test_delta__outputs_rich_table_when_json_option_is_not_used(
    call_ops2deb, cache_path, lockfile_paths
):
    # Given
    configuration = """\
    # lockfile=ops2deb-0.lock.yml
    name: great-app
    version: 1.1.1
    summary: this file is not locked
    fetch: http://testserver/{{version}}/great-app.tar.gz
    """

    expected_output_lines = [
        "-  great-app   1.0.0-1~ops2deb   all  ",
        "-  kube-score  1.12.0-1~ops2deb  amd64",
        "-  super-app   1.0.0-1~ops2deb   all  ",
        "+  great-app   1.1.1-1~ops2deb   amd64",
        "",
    ]

    # When
    result = call_ops2deb(
        "delta", "-r", "http://deb.wakemeops.com stable", configurations=[configuration]
    )

    # Then
    assert result.exit_code == 0
    assert result.stderr.split("\n")[3:] == expected_output_lines


def test_delta__outputs_a_json_when_json_option_is_used(
    call_ops2deb, cache_path, lockfile_paths
):
    # Given
    configuration = """\
    # lockfile=ops2deb-0.lock.yml
    name: great-app
    version: 1.1.1
    summary: this file is not locked
    fetch: http://testserver/{{version}}/great-app.tar.gz
    """

    expected_output_dict = {
        "added": [
            {"architecture": "amd64", "name": "great-app", "version": "1.1.1-1~ops2deb"}
        ],
        "removed": [
            {"architecture": "all", "name": "great-app", "version": "1.0.0-1~ops2deb"},
            {
                "architecture": "amd64",
                "name": "kube-score",
                "version": "1.12.0-1~ops2deb",
            },
            {"architecture": "all", "name": "super-app", "version": "1.0.0-1~ops2deb"},
        ],
    }

    # When
    result = call_ops2deb(
        "delta",
        "-r",
        "http://deb.wakemeops.com stable",
        "--json",
        configurations=[configuration],
    )

    # Then
    assert result.exit_code == 0
    assert json.loads(result.stdout) == expected_output_dict


@pytest.mark.parametrize(
    "subcommand", ["update", "generate", "format", "validate", "lock", "delta"]
)
def test_ops2deb__exits_with_error_code_when_configuration_file_has_invalid_yaml(
    call_ops2deb, subcommand, configuration_path
):
    # Given
    os.environ["OPS2DEB_REPOSITORY"] = "http://deb.wakemeops.com stable"
    configuration = """\
    - name: awesome-metapackage
        version: 1.0.0
    """

    # When
    result = call_ops2deb(subcommand, configurations=[configuration])

    # Then
    assert f"Failed to parse {configuration_path}" in result.stderr
    assert result.exit_code == 77


@pytest.mark.parametrize(
    "subcommand", ["update", "generate", "format", "validate", "lock", "delta"]
)
def test_ops2deb__exits_with_error_code_when_configuration_file_has_validation_error(
    call_ops2deb, subcommand, configuration_path
):
    # Given
    os.environ["OPS2DEB_REPOSITORY"] = "http://deb.wakemeops.com stable"
    configuration = """\
    - name: awesome-metapackage
    """

    # When
    result = call_ops2deb(subcommand, configurations=[configuration])

    # Then
    assert f"ailed to parse blueprint at index 0 in {configuration_path}" in result.stderr
    assert result.exit_code == 77
