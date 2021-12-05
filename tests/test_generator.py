import pytest

from ops2deb.generator import generate
from ops2deb.parser import Blueprint

blueprint_1 = Blueprint(
    name="great-app",
    version="1.0.0",
    homepage="http://great-app.io",
    revision="3",
    arch="all",
    summary="My great app",
    description="A detailed description of the super package",
    depends=["package_a"],
    recommends=["package_b"],
    conflicts=["package_c"],
)

control_1 = """Source: great-app
Priority: optional
Maintainer: ops2deb <ops2deb@upciti.com>
Build-Depends: debhelper
Standards-Version: 3.9.6
Homepage: http://great-app.io

Package: great-app
Architecture: all
Depends: package_a
Recommends: package_b
Conflicts: package_c
Description: My great app
 A detailed description of the super package"""

blueprint_2 = Blueprint(
    name="great-app",
    version="1.0.0",
    revision="3",
    arch="all",
    summary="My great app",
    description="A detailed description of the super package",
    depends=["package_a"],
    conflicts=["package_c"],
)


control_2 = """Source: great-app
Priority: optional
Maintainer: ops2deb <ops2deb@upciti.com>
Build-Depends: debhelper
Standards-Version: 3.9.6

Package: great-app
Architecture: all
Depends: package_a
Conflicts: package_c
Description: My great app
 A detailed description of the super package"""


blueprint_3 = Blueprint(
    name="great-app",
    version="1.0.0",
    summary="My great app",
    description="A detailed description of the super package",
)


control_3 = """Source: great-app
Priority: optional
Maintainer: ops2deb <ops2deb@upciti.com>
Build-Depends: debhelper
Standards-Version: 3.9.6

Package: great-app
Architecture: amd64
Description: My great app
 A detailed description of the super package"""


@pytest.mark.parametrize(
    "blueprint, control",
    [(blueprint_1, control_1), (blueprint_2, control_2), (blueprint_3, control_3)],
)
def test_generate_should_produce_identical_control_file_snapshot(
    tmp_path, blueprint, control
):
    generate([blueprint], tmp_path)
    control_file = tmp_path / f"great-app_1.0.0_{blueprint.arch}/debian/control"
    assert control_file.read_text() == control
