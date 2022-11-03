import logging
import shutil
import subprocess
from pathlib import Path

from dirsync import sync

from ops2deb import logger
from ops2deb.apt import DebianRepositoryPackage, sync_list_repository_packages
from ops2deb.exceptions import Ops2debGeneratorError, Ops2debGeneratorScriptError
from ops2deb.fetcher import FetchResult, fetch_remote_files
from ops2deb.parser import Blueprint, HereDocument, SourceDestinationStr, extend
from ops2deb.templates import environment
from ops2deb.utils import working_directory


def _format_command_output(output: str) -> str:
    lines = output.splitlines()
    output = "\n  ".join([line for line in lines])
    return "> " + output


class SourcePackage:
    def __init__(self, blueprint: Blueprint, output_directory: Path):
        epoch = f"{blueprint.epoch}:" if blueprint.epoch else ""
        self.debian_version = f"{epoch}{blueprint.version}-{blueprint.revision}~ops2deb"
        self.directory_name = f"{blueprint.name}_{blueprint.version}_{blueprint.arch}"
        self.package_directory = (output_directory / self.directory_name).absolute()
        self.debian_directory = self.package_directory / "debian"
        self.source_directory = self.package_directory / "src"
        self.fetch_directory = self.package_directory / "fetched"
        self.blueprint = blueprint
        self.remote_file = self.blueprint.render_fetch()

    def _render_template(self, template_name: str) -> None:
        template = environment.get_template(f"{template_name}")
        package = self.blueprint.dict(exclude={"fetch", "script"})
        package.update({"version": self.debian_version})
        template.stream(package=package).dump(str(self.debian_directory / template_name))

    def _init(self) -> None:
        shutil.rmtree(self.debian_directory, ignore_errors=True)
        self.debian_directory.mkdir(parents=True)
        shutil.rmtree(self.fetch_directory, ignore_errors=True)
        shutil.rmtree(self.source_directory, ignore_errors=True)
        self.source_directory.mkdir(parents=True)
        for path in ["usr/bin", "usr/share", "usr/lib"]:
            (self.source_directory / path).mkdir(parents=True)

    def _populate_with_fetch_result(self, fetch_result: FetchResult) -> None:
        if fetch_result.storage_path.is_file():
            self.fetch_directory.mkdir(exist_ok=True)
            shutil.copy2(fetch_result.storage_path, self.fetch_directory)
        else:
            dirsync_logger = logging.getLogger("dirsync")
            dirsync_logger.setLevel(logging.CRITICAL)
            # FIXME: is there a more recent lib to sync trees?
            #  shutil.copytree is not an option: https://bugs.python.org/issue38523
            sync(
                str(fetch_result.storage_path),
                str(self.fetch_directory),
                "sync",
                create=True,
                logger=dirsync_logger,
            )

    def _install_here_document(self, entry: HereDocument, destination: Path) -> None:
        if destination.exists():
            raise Ops2debGeneratorError(f"Destination {destination} already exists")
        destination.write_text(self.blueprint.render_string(entry.content))

    def _install_source_destination_str(
        self, entry: SourceDestinationStr, destination: Path
    ) -> None:
        source = Path(self.blueprint.render_string(entry.source))
        if source.exists() is False:
            raise Ops2debGeneratorError(f"Source {str(source)} does not exist")
        if source.is_dir() is True:
            shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=True)
        elif source.is_file() is True:
            shutil.copy2(source, destination)
        else:
            raise Ops2debGeneratorError(
                f"Source {str(source)} is not a file nor a directory"
            )

    def _render_string(self, string: str) -> str:
        return self.blueprint.render_string(
            string,
            src=self.source_directory,
            debian=self.debian_directory,
        )

    def _install_files(self) -> None:
        for entry in self.blueprint.install:
            destination = Path(self._render_string(entry.destination))
            if (
                destination.is_absolute() is True
                and destination.is_relative_to(self.package_directory) is False
            ):
                destination = self.source_directory / destination.relative_to("/")
            if destination.is_absolute() is False:
                destination = self.package_directory / destination
            destination.parent.mkdir(parents=True, exist_ok=True)

            if isinstance(entry, HereDocument):
                self._install_here_document(entry, destination)
            elif isinstance(entry, SourceDestinationStr):
                self._install_source_destination_str(entry, destination)

    def _run_script(self) -> None:
        for line in self.blueprint.script:
            line = self._render_string(line)
            logger.info(f"$ {line}")
            result = subprocess.run(line, shell=True, capture_output=True)
            if stdout := result.stdout.decode():
                logger.info(_format_command_output(stdout))
            if stderr := result.stderr.decode():
                logger.error(_format_command_output(stderr))
            if result.returncode:
                raise Ops2debGeneratorScriptError

    def generate(self, fetch_results: dict[str, FetchResult]) -> None:
        fetch_result: FetchResult | None = None
        if self.remote_file is not None:
            fetch_result = fetch_results.get(self.remote_file.url, None)
            if fetch_result is None:
                return

        logger.title(f"Generating source package {self.directory_name}...")

        # make sure we generate source packages in a clean environment
        # without artifacts from previous builds
        self._init()

        # copy downloaded/extracted archive to package fetch directory
        if fetch_result is not None:
            self._populate_with_fetch_result(fetch_result)

        # render debian/* files
        for template in [
            "changelog",
            "control",
            "rules",
            "compat",
            "install",
            "lintian-overrides",
        ]:
            self._render_template(template)

        # if blueprint has no fetch instruction, we stay in the directory from which
        # ops2deb was called, otherwise we run install and script from the fetch directory
        cwd = self.fetch_directory if self.blueprint.fetch else Path(".")

        with working_directory(cwd):
            # copy files / create here documents
            self._install_files()
            # run blueprint script
            self._run_script()


def filter_already_published_packages(
    packages: list[SourcePackage], debian_repository: str
) -> list[SourcePackage]:
    already_published_packages = sync_list_repository_packages(debian_repository)
    filtered_packages: list[SourcePackage] = []
    for package in packages:
        if (
            DebianRepositoryPackage(
                package.blueprint.name, package.debian_version, package.blueprint.arch
            )
            not in already_published_packages
        ):
            filtered_packages.append(package)
    return filtered_packages


def generate(
    blueprints: list[Blueprint],
    output_directory: Path,
    debian_repository: str | None = None,
    only_names: list[str] | None = None,
) -> list[SourcePackage]:
    # each blueprint can yield multiple source packages, one per supported arch
    packages = [SourcePackage(b, output_directory) for b in extend(blueprints)]

    if only_names is not None:
        packages = [p for p in packages if p.blueprint.name in only_names]

    # filter out packages already available in the debian repository
    if debian_repository is not None and packages:
        packages = filter_already_published_packages(packages, debian_repository)

    # run fetch instructions (download, verify, extract) in parallel
    files = [p.remote_file for p in packages if p.remote_file is not None]
    fetch_results, fetch_errors = fetch_remote_files(files)

    for package in packages:
        package.generate(fetch_results)

    if fetch_errors:
        raise Ops2debGeneratorError(f"{len(fetch_errors)} failures occurred")

    return packages
