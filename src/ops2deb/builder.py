import asyncio
import hashlib
import shutil
import tarfile
from pathlib import Path
from typing import Any

import unix_ar
from debian.changelog import Changelog
from debian.deb822 import Deb822

from ops2deb import logger
from ops2deb.exceptions import Ops2debBuilderError
from ops2deb.templates import environment
from ops2deb.utils import log_and_raise, working_directory


def parse_version_from_changelog(cwd: Path) -> str:
    raw_changelog = (cwd / "debian" / "changelog").read_text()
    changelog = Changelog(raw_changelog)
    if changelog.version is None:
        raise Ops2debBuilderError("Could not read package version from changelog")
    return str(changelog.version)


def parse_debian_control(cwd: Path) -> dict[str, str]:
    """
    Extract fields from debian/control
    :param cwd: Path to debian source package
    :return: Dict object with fields as keys
    """

    raw_control = (cwd / "debian" / "control").read_text()
    parsed_control: dict[str, str] = {}
    # FIXME: will not work if control defines multiple packages
    for paragraph in Deb822.iter_paragraphs(raw_control):
        parsed_control.update(paragraph)
    parsed_control.pop("Source")
    return parsed_control


def _make_tar_xz_archive(archive_path: Path, base_path: Path) -> None:
    archive_path.parent.mkdir(exist_ok=True, parents=True)
    tar_compression = "xz"

    def _set_uid_gid(tarinfo: Any) -> Any:
        tarinfo.gid = 0
        tarinfo.gname = "root"
        tarinfo.uid = 0
        tarinfo.uname = "root"
        return tarinfo

    tar = tarfile.open(archive_path, f"w|{tar_compression}")

    try:
        with working_directory(base_path):
            tar.add(".", filter=_set_uid_gid)
    finally:
        tar.close()


def _make_ar_archive(archive_path: Path, base_path: Path) -> None:
    if archive_path.is_file():
        archive_path.unlink()
    else:
        archive_path.parent.mkdir(exist_ok=True, parents=True)
    ar = unix_ar.open(str(archive_path), "w")
    try:
        with working_directory(base_path):
            for file in Path(".").glob("*"):
                ar.addfile(str(file))
    finally:
        ar.close()


def _fix_permissions(source_path: Path) -> None:
    for path in source_path.rglob("*"):
        if path.is_dir():
            path.chmod(0o0755)
        else:
            path.chmod(0o0644)

    for bin_paths in ["usr/bin/*", "usr/sbin/*", "bin/*", "sbin/*"]:
        for path in source_path.rglob(bin_paths):
            if path.is_file() or path.is_symlink():
                path.chmod(0o0755)


def _compute_installed_size(source_path: Path) -> int:
    size = 0
    for path in source_path.rglob("*"):
        size += path.stat(follow_symlinks=False).st_size
    return int(size / 1024)


def _compute_md5_sum(file_path: Path) -> str:
    md5_hash = hashlib.md5()
    with file_path.open("rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            md5_hash.update(byte_block)
    return md5_hash.hexdigest()


def _build_md5sums(source_path: Path, control_path: Path) -> None:
    result = ""
    for path in source_path.rglob("*"):
        if path.is_file():
            result += f"{_compute_md5_sum(path)}  {path.relative_to(source_path)}\n"
    md5sums_path = control_path / "md5sums"
    md5sums_path.write_text(result)
    md5sums_path.chmod(0o644)


def _build_package(package_path: Path) -> None:
    """
    Simple implementation of a debian package builder
    Can be used instead of dpkg-buildpackage to speed up
    the build process.
    """

    control = parse_debian_control(package_path)
    version = parse_version_from_changelog(package_path)
    build_directory_name = f"{control['Package']}_{version}_{control['Architecture']}"
    build_path = Path("/tmp/ops2deb_builder") / build_directory_name
    source_path = package_path / "src"

    if build_path.exists():
        shutil.rmtree(build_path)

    control_path = build_path / "control"
    control_path.mkdir(exist_ok=True, parents=True)
    installed_size = _compute_installed_size(source_path)
    template = environment.get_template("package-control")
    template.stream(control=control, version=version, installed_size=installed_size).dump(
        str(control_path / "control")
    )

    _build_md5sums(source_path, control_path)
    _fix_permissions(source_path)

    # create content of .deb tar archive
    tar_path = build_path / "tar"
    tar_path.mkdir(exist_ok=True, parents=True)
    (tar_path / "debian-binary").write_text("2.0\n")
    _make_tar_xz_archive(tar_path / "data.tar.xz", source_path)
    _make_tar_xz_archive(tar_path / "control.tar.xz", control_path)

    # create final .deb tar archive
    control = parse_debian_control(package_path)
    package_name = f'{control["Package"]}_{version}_{control["Architecture"]}'
    _make_ar_archive(package_path.parent / f"{package_name}.deb", tar_path)


async def build_package(package_path: Path) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _build_package, package_path)


async def build_package_with_dpkgbuildpackage(cwd: Path) -> None:
    """Run dpkg-buildpackage in specified path."""
    args = ["-us", "-uc"]
    arch = parse_debian_control(cwd)["Architecture"]
    if arch != "all":
        args += ["--host-arch", arch]

    proc = await asyncio.create_subprocess_exec(
        "/usr/bin/dpkg-buildpackage",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if stdout:
        logger.debug(stdout.decode())
    if stderr:
        logger.debug(stderr.decode())

    if proc.returncode:
        log_and_raise(Ops2debBuilderError(f"Failed to build package in {str(cwd)}"))


def build(package_paths: list[Path], workers: int) -> None:
    """
    Build debian source packages in parallel.
    :param package_paths: list of debian source package paths
    :param workers: Number of threads to run in parallel
    """

    logger.title(f"Building {len(package_paths)} source packages...")

    async def _builder(package_path: Path) -> None:
        logger.info(f"Building {package_path}...")
        # await build_package_with_dpkgbuildpackage(package_path)
        await build_package(package_path)
        logger.info(f"Successfully built {package_path}")

    async def _build_package(sem: asyncio.Semaphore, _path: Path) -> None:
        async with sem:  # semaphore limits num of simultaneous builds
            await _builder(_path)

    async def _build_packages() -> Any:
        sem = asyncio.Semaphore(workers)
        return await asyncio.gather(
            *[_build_package(sem, p) for p in package_paths], return_exceptions=False
        )

    results = asyncio.run(_build_packages())

    if errors := [e for e in results if isinstance(e, Exception)]:
        raise Ops2debBuilderError(f"{len(errors)} failures occurred")


def build_all(output_directory: Path, workers: int) -> None:
    """
    Build debian source packages in parallel.
    :param output_directory: path where to search for source packages
    :param workers: Number of threads to run in parallel
    """
    if output_directory.exists() is False:
        raise Ops2debBuilderError(f"Directory {output_directory} does not exist")

    if output_directory.is_dir() is False:
        raise Ops2debBuilderError(f"{output_directory} is not a directory")

    paths = []
    for output_directory in output_directory.iterdir():
        if output_directory.is_dir() and (output_directory / "debian/control").is_file():
            paths.append(output_directory)
    build(paths, workers)
