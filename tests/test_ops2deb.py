import base64
from typing import Optional

import httpx
import pytest
import ruamel.yaml
from starlette.applications import Starlette
from starlette.responses import Response
from typer.testing import CliRunner

from ops2deb.cli import app
from ops2deb.parser import load, parse

yaml = ruamel.yaml.YAML(typ="safe")
runner = CliRunner()

starlette_app = Starlette(debug=True)


@starlette_app.route("/1.0.0/great-app.tar.gz")
@starlette_app.route("/1.1.0/great-app.tar.gz")
@starlette_app.route("/1.1.1/great-app.tar.gz")
async def download_tar_gz(request):
    # b64 encoded tar.gz with an empty "great-app" file
    dummy_tar_gz_file = (
        b"H4sIAAAAAAAAA+3OMQ7CMBAEQD/FH0CyjSy/xwVCFJAoCf/HFCAqqEI1U9yudF"
        b"fceTn17dDnOewnDa3VZ+ZW02e+hHxsrYxRagkp59FDTDv+9HZft77EGNbLdbp9uf"
        b"u1BwAAAAAAAAAAgD96AGPmdYsAKAAA"
    )
    return Response(
        base64.b64decode(dummy_tar_gz_file),
        status_code=200,
        media_type="application/x-gzip",
    )


@starlette_app.route("/1.0.0/super-app.zip")
async def download_zip(request):
    dummy_zip_file = (
        b"UEsDBBQACAAIAFVdkFIAAAAAAAAAAAAAAAAJACAAZ3JlYXQtYXBwVVQNAAcTXHlgE1x5YBNceWB1"
        b"eAsAAQToAwAABOgDAAADAFBLBwgAAAAAAgAAAAAAAABQSwECFAMUAAgACABVXZBSAAAAAAIAAAAA"
        b"AAAACQAgAAAAAAAAAAAAtIEAAAAAZ3JlYXQtYXBwVVQNAAcTXHlgE1x5YBNceWB1eAsAAQToAwAA"
        b"BOgDAABQSwUGAAAAAAEAAQBXAAAAWQAAAAAA"
    )
    return Response(
        base64.b64decode(dummy_zip_file),
        status_code=200,
        media_type="application/zip",
    )


@pytest.fixture(scope="session", autouse=True)
def mock_httpx_client():
    real_async_client = httpx.AsyncClient

    def async_client_mock(**kwargs):
        return real_async_client(app=starlette_app, **kwargs)

    httpx.AsyncClient = async_client_mock
    yield
    httpx.AsyncClient = real_async_client


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


@pytest.fixture(scope="function")
def call_ops2deb(tmp_path):
    def _invoke(*extra_args, configuration: Optional[str] = None):
        configuration_path = tmp_path / "ops2deb.yml"
        configuration_path.write_text(configuration or mock_valid_configuration)
        return runner.invoke(
            app,
            [
                "--verbose",
                "--work-dir",
                str(tmp_path),
                "--cache-dir",
                str(tmp_path / "cache"),
                "--config",
                str(tmp_path / "ops2deb.yml"),
            ]
            + [*extra_args],
        )

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


def test_ops2deb_generate_should_fail_if_archive_not_found(tmp_path, call_ops2deb):
    result = call_ops2deb(
        "generate", configuration=mock_configuration_with_archive_not_found
    )
    print(result.stdout)
    assert "404" in result.stdout
    assert result.exit_code == 1


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
    assert configuration.__root__[0].version == "1.1.1"


def test_ops2deb_update_should_reset_blueprint_revision_to_one(tmp_path, call_ops2deb):
    call_ops2deb("update")
    configuration = load(tmp_path / "ops2deb.yml", yaml)
    assert configuration[0]["revision"] == 1
    assert "revision" not in configuration[1]
