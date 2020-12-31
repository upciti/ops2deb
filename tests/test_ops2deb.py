import base64
from copy import deepcopy

import httpx
import yaml
import pytest
from starlette.applications import Starlette
from starlette.responses import Response
from typer.testing import CliRunner

from ops2deb.cli import app
from ops2deb.parser import Configuration
from ops2deb.generator import generate

runner = CliRunner()

# b64 encoded tar.gz with an empty "great-app" file
dummy_tar_gz_file = (
    b"H4sIAAAAAAAAA+3OMQ7CMBAEQD/FH0CyjSy/xwVCFJAoCf/HFCAqqEI1U9yudF"
    b"fceTn17dDnOewnDa3VZ+ZW02e+hHxsrYxRagkp59FDTDv+9HZft77EGNbLdbp9uf"
    b"u1BwAAAAAAAAAAgD96AGPmdYsAKAAA"
)

dummy_config = """
- name: mypackage
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

dummy_config_dict = yaml.safe_load(dummy_config)
starlette_app = Starlette(debug=True)


@starlette_app.route("/1.0.0/great-app.tar.gz")
@starlette_app.route("/1.1.0/great-app.tar.gz")
@starlette_app.route("/1.1.1/great-app.tar.gz")
async def great_app(request):
    return Response(
        base64.b64decode(dummy_tar_gz_file),
        status_code=200,
        media_type="application/x-gzip",
    )


@pytest.fixture
def mock_httpx_client():
    real_async_client = httpx.AsyncClient

    def async_client_mock(**kwargs):
        return real_async_client(app=starlette_app, **kwargs)
    httpx.AsyncClient = async_client_mock
    yield
    httpx.AsyncClient = real_async_client


def test_ops2deb(tmp_path, mock_httpx_client):
    # purge download cache
    result = runner.invoke(
        app, ["purge"]
    )
    print(result.stdout)
    assert result.exit_code == 0

    # generate dummy source package
    config = tmp_path / "ops2deb.yml"
    config.write_text(dummy_config)
    result = runner.invoke(
        app, ["-w", str(tmp_path), "-c", str(tmp_path / "ops2deb.yml"), "generate"]
    )
    print(result.stdout)
    assert result.exit_code == 0
    assert (tmp_path / "mypackage_1.0.0_all/src/usr/bin/great-app").is_file()
    assert (tmp_path / "mypackage_1.0.0_all/debian/control").is_file()

    # re-generate, but this time nothing should be downloaded
    result = runner.invoke(
        app, ["-w", str(tmp_path), "-c", str(tmp_path / "ops2deb.yml"), "generate"]
    )
    assert "Downloading" not in result.stdout

    # build dummy source package
    result = runner.invoke(app, ["-v", "-w", str(tmp_path), "build"])
    print(result.stdout)
    assert result.exit_code == 0
    assert (tmp_path / "mypackage_1.0.0-1~ops2deb_all.deb").is_file()

    # check if dummy source package has new releases
    result = runner.invoke(app, ["-v", "-c", str(tmp_path / "ops2deb.yml"), "update"])
    print(result.stdout)
    assert result.exit_code == 0


def test_invalid_file_checksum():
    config = deepcopy(dummy_config_dict)
    config[0]['fetch']['sha256'] = "deadbeef"
    with pytest.raises(ValueError, match="Wrong checksum for file great-app.tar.gz."):
        generate(Configuration.parse_obj(config).__root__)
