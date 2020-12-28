import base64

from starlette.applications import Starlette
from starlette.responses import Response
from typer.testing import CliRunner

from debops.cli import app
from debops.fetcher import set_app

runner = CliRunner()

# b64 encoded tar.gz with an empty "great-app" file
dummy_tar_gz_file = (
    b"H4sIAAAAAAAAA+3OMQ7CMBAEQD/FH0CyjSy/xwVCFJAoCf/HFCAqqEI1U9yudF"
    b"fceTn17dDnOewnDa3VZ+ZW02e+hHxsrYxRagkp59FDTDv+9HZft77EGNbLdbp9uf"
    b"u1BwAAAAAAAAAAgD96AGPmdYsAKAAA"
)

dummy_config = """
- name: mypackage
  version: 1.0
  arch: all
  summary: Great package
  description: |
    A detailed description of the great package 
  fetch:
    url: http://testserver/great-app.tar.gz
    sha256: f1be6dd36b503641d633765655e81cdae1ff8f7f73a2582b7468adceb5e212a9
  script:
    - install -d {{src}}/usr/bin/
    - mv great-app {{src}}/usr/bin/great-app
"""

starlette_app = Starlette(debug=True)
set_app(starlette_app)


@starlette_app.route("/great-app.tar.gz")
async def great_app(request):
    return Response(
        base64.b64decode(dummy_tar_gz_file),
        status_code=200,
        media_type="application/x-gzip",
    )


def test_debops(tmp_path):
    # generate dummy source package
    config = tmp_path / "debops.yml"
    config.write_text(dummy_config)
    result = runner.invoke(
        app, ["-w", str(tmp_path), "-c", str(tmp_path / "debops.yml"), "generate"]
    )

    print(result.stdout)
    assert result.exit_code == 0
    assert (tmp_path / "mypackage_1.0_all/src/usr/bin/great-app").is_file()
    assert (tmp_path / "mypackage_1.0_all/debian/control").is_file()

    # build dummy source package
    result = runner.invoke(app, ["-w", str(tmp_path), "build"])

    assert result.exit_code == 0
    assert (tmp_path / "mypackage_1.0-1~debops_all.deb").is_file()
