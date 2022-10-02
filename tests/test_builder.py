from textwrap import dedent

from ops2deb.builder import parse_debian_control, parse_version_from_changelog


def test_parse_debian_control__should_return_dict_with_all_fieds_from_debian_control(
    tmp_path,
):
    raw_control = dedent(
        """\
    Source: great-app
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
    )
    (tmp_path / "debian").mkdir()
    (tmp_path / "debian" / "control").write_text(raw_control)
    control = parse_debian_control(tmp_path)
    assert control == {
        "Priority": "optional",
        "Maintainer": "ops2deb <ops2deb@upciti.com>",
        "Build-Depends": "debhelper, build-dep-1, build-dep-2",
        "Standards-Version": "3.9.6",
        "Homepage": "http://great-app.io",
        "Package": "great-app",
        "Architecture": "all",
        "Provides": "virtual_package",
        "Depends": "package_a",
        "Recommends": "package_b",
        "Replaces": "package_c",
        "Conflicts": "package_d",
        "Description": "My great app\n A detailed description of the super package",
    }


def test_parse_version_from_changelog__should_return_package_version(
    tmp_path,
):
    raw_changelog = dedent(
        """\
    ops2deb (0.18.0-1~ops2deb) stable; urgency=medium

      * Release 0.18.0-1~ops2deb

     -- ops2deb <ops2deb@upciti.com>  Tue, 07 May 2019 20:31:30 +0000
    """
    )
    (tmp_path / "debian").mkdir()
    (tmp_path / "debian" / "changelog").write_text(raw_changelog)
    version = parse_version_from_changelog(tmp_path)
    assert version == "0.18.0-1~ops2deb"
