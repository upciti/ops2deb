import shutil

import pytest

from ops2deb.exceptions import Ops2debGeneratorError
from ops2deb.generator import BASE_TEMPORARY_DIRECTORY, SourcePackage
from ops2deb.parser import Blueprint

blueprint_1 = Blueprint(
    name="great-app",
    version="1.0.0",
    homepage="http://great-app.io",
    revision="3",
    architecture="all",
    summary="My great app",
    description="A detailed description of the super package",
    build_depends=["build-dep-1", "build-dep-2"],
    provides=["virtual_package"],
    depends=["package_a"],
    recommends=["package_b"],
    replaces=["package_c"],
    conflicts=["package_d"],
)

control_1 = """Source: great-app
Priority: optional
Maintainer: ops2deb <ops2deb@upciti.com>
Build-Depends: debhelper, build-dep-1, build-dep-2
Standards-Version: 3.9.6
Homepage: http://great-app.io

Package: great-app
Architecture: all
Provides: virtual_package
Depends: package_a
Recommends: package_b
Replaces: package_c
Conflicts: package_d
Description: My great app
 A detailed description of the super package
"""

blueprint_2 = Blueprint(
    name="great-app",
    version="1.0.0",
    revision="3",
    architecture="all",
    summary="My great app",
    description="A detailed description of the super package.\n"
    "This description has two lines.",
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
 A detailed description of the super package.
 This description has two lines.
"""


blueprint_3 = Blueprint(
    name="great-app",
    version="1.0.0",
    summary="My great app",
    description="What should empty lines be replaced with?\n\nWith dots!",
)


control_3 = """Source: great-app
Priority: optional
Maintainer: ops2deb <ops2deb@upciti.com>
Build-Depends: debhelper
Standards-Version: 3.9.6

Package: great-app
Architecture: amd64
Description: My great app
 What should empty lines be replaced with?
 .
 With dots!
"""


blueprint_4 = Blueprint(
    name="great-app",
    version="1.0.0",
    summary="My great app without a description",
)


control_4 = """Source: great-app
Priority: optional
Maintainer: ops2deb <ops2deb@upciti.com>
Build-Depends: debhelper
Standards-Version: 3.9.6

Package: great-app
Architecture: amd64
Description: My great app without a description
"""


@pytest.mark.parametrize(
    "blueprint, control",
    [
        (blueprint_1, control_1),
        (blueprint_2, control_2),
        (blueprint_3, control_3),
        (blueprint_4, control_4),
    ],
)
def test_generate_should_produce_same_control_file_content_as_the_snapshot(
    tmp_path, blueprint, control
):
    SourcePackage(blueprint, tmp_path, tmp_path).generate({})
    control_file = tmp_path / f"great-app_1.0.0_{blueprint.architecture}/debian/control"
    assert control_file.read_text() == control


def test__render_string_should_render_tmp_jinja_variable(tmp_path, blueprint_factory):
    blueprint = blueprint_factory()
    string = SourcePackage(blueprint, tmp_path, tmp_path)._render_string("{{tmp}}/test")
    assert string == "/tmp/ops2deb_tmp/great-app_1.0.0_amd64/test"


def test_generate_should_create_blueprint_install_here_document_in_src_directory_if_destination_is_absolute(  # noqa: E501
    tmp_path, blueprint_factory
):
    files = [dict(path="/test", content="content")]
    blueprint = blueprint_factory(fetch=None, install=files, script=[])
    SourcePackage(blueprint, tmp_path, tmp_path).generate({})
    assert (tmp_path / "great-app_1.0.0_amd64/src/test").is_file()


def test_generate_should_create_blueprint_install_here_document_in_package_directory_if_destination_is_relative(  # noqa: E501
    tmp_path, blueprint_factory
):
    files = [dict(path="test", content="content")]
    blueprint = blueprint_factory(fetch=None, install=files, script=[])
    SourcePackage(blueprint, tmp_path, tmp_path).generate({})
    assert (tmp_path / "great-app_1.0.0_amd64/test").is_file()


def test_generate_should_render_content_of_blueprint_install_here_document(
    tmp_path, blueprint_factory
):
    files = [dict(path="test", content="{{version}}")]
    blueprint = blueprint_factory(fetch=None, install=files, script=[])
    SourcePackage(blueprint, tmp_path, tmp_path).generate({})
    assert (tmp_path / "great-app_1.0.0_amd64/test").read_text() == blueprint.version


def test_generate_should_fail_to_create_blueprint_install_here_document_if_file_already_exists(  # noqa: E501
    tmp_path, blueprint_factory
):
    files = [dict(path="/test", content="content"), dict(path="/test", content="content")]
    blueprint = blueprint_factory(fetch=None, install=files, script=[])
    with pytest.raises(Ops2debGeneratorError) as exc:
        SourcePackage(blueprint, tmp_path, tmp_path).generate({})
    assert exc.match("Failed to write .+, file already exists")


def test_generate_should_copy_file_when_blueprint_install_contains_a_source_destination_str(  # noqa: E501
    tmp_path, blueprint_factory
):
    source = tmp_path / "test"
    source.write_text("test")
    blueprint = blueprint_factory(fetch=None, install=[f"{source}:/test"], script=[])
    SourcePackage(blueprint, tmp_path, tmp_path).generate({})
    assert (tmp_path / "great-app_1.0.0_amd64/src/test").is_file()
    assert (tmp_path / "great-app_1.0.0_amd64/src/test").read_text() == "test"


def test_generate_should_copy_dir_tree_when_blueprint_install_contains_a_source_destination_str_and_source_is_a_dir(  # noqa: E501
    tmp_path, blueprint_factory
):
    source = tmp_path / "test"
    tree = source / "usr" / "share" / "a"
    tree.mkdir(parents=True)
    (tree / "test").write_text("test")
    blueprint = blueprint_factory(fetch=None, install=[f"{source}:/"], script=[])
    SourcePackage(blueprint, tmp_path, tmp_path).generate({})
    assert (tmp_path / "great-app_1.0.0_amd64/src/usr/share/a/test").is_file()
    assert (tmp_path / "great-app_1.0.0_amd64/src/usr/share/a/test").read_text() == "test"


def test_generate_should_fail_when_blueprint_install_contains_a_source_destination_str_and_source_does_not_exist(  # noqa: E501
    tmp_path, blueprint_factory
):
    files = [f"{tmp_path}/test:/test"]
    blueprint = blueprint_factory(fetch=None, install=files, script=[])
    with pytest.raises(Ops2debGeneratorError) as exc:
        SourcePackage(blueprint, tmp_path, tmp_path).generate({})
    assert exc.match("Failed to copy .+/test, it does not exist")


def test_generate_should_render_cwd_and_debian_variables_in_blueprint_install_source_destination_str(  # noqa: E501
    tmp_path, blueprint_factory
):
    (tmp_path / "test").touch()
    files = ["{{cwd}}/test:{{debian}}/test"]
    blueprint = blueprint_factory(fetch=None, install=files, script=[])
    SourcePackage(blueprint, tmp_path, tmp_path).generate({})
    assert (tmp_path / "great-app_1.0.0_amd64/debian/test").is_file()


def test_generate_should_render_and_tmp_variables_in_blueprint_install_here_document(
    tmp_path, blueprint_factory
):
    shutil.rmtree(BASE_TEMPORARY_DIRECTORY, ignore_errors=True)
    files = [dict(path="{{tmp}}/test", content="{{tmp}}")]
    blueprint = blueprint_factory(fetch=None, install=files, script=[])
    SourcePackage(blueprint, tmp_path, tmp_path).generate({})
    content = (BASE_TEMPORARY_DIRECTORY / "great-app_1.0.0_amd64/test").read_text()
    assert content == "/tmp/ops2deb_tmp/great-app_1.0.0_amd64"


def test__init__should_use_absolute_paths(blueprint_factory, tmp_path):
    blueprint = blueprint_factory()
    package = SourcePackage(blueprint, tmp_path, tmp_path)
    assert package.package_directory.is_absolute() is True
    assert package.debian_directory.is_absolute() is True
    assert package.source_directory.is_absolute() is True
    assert package.configuration_directory.is_absolute() is True
