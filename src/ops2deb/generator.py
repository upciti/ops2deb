import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from . import logger
from .apt import DebianRepositoryPackage, sync_list_repository_packages
from .exceptions import Ops2debGeneratorError, Ops2debGeneratorScriptError
from .fetcher import Fetcher, FetchResult, FetchResultOrError
from .parser import Blueprint, extend
from .templates import environment
from .utils import separate_successes_from_errors


def _format_command_output(output: str) -> str:
    lines = output.splitlines()
    output = "\n  ".join([line for line in lines])
    return "> " + output


class SourcePackage:
    def __init__(self, blueprint: Blueprint, output_directory: Path):
        self.debian_version = f"{blueprint.version}-{blueprint.revision}~ops2deb"
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
            shutil.copytree(fetch_result.storage_path, self.fetch_directory)

    def _run_script(self) -> None:
        # if blueprint has no fetch instruction, we stay in the directory from which
        # ops2deb was called
        cwd = self.fetch_directory if self.blueprint.fetch else Path(".")

        # run script
        for line in self.blueprint.render_script(src=self.source_directory):
            logger.info(f"$ {line}")
            result = subprocess.run(line, shell=True, cwd=cwd, capture_output=True)
            if stdout := result.stdout.decode():
                logger.info(_format_command_output(stdout))
            if stderr := result.stderr.decode():
                logger.error(_format_command_output(stderr))
            if result.returncode:
                raise Ops2debGeneratorScriptError

    def generate(self, fetch_results: Dict[str, FetchResultOrError]) -> None:
        fetch_result: Optional[FetchResultOrError] = None
        if self.remote_file is not None:
            fetch_result = fetch_results[self.remote_file.url]
            if not isinstance(fetch_result, FetchResult):
                # fetch failed, we cannot generate source package
                return

        logger.title(f"Generating source package {self.directory_name}...")

        # make sure we generate source packages in a clean environment
        # without artifacts from previous builds
        self._init()

        # copy downloaded/extracted archive to package fetch directory
        if isinstance(fetch_result, FetchResult):
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

        # run blueprint script
        self._run_script()


def filter_already_published_packages(
    packages: List[SourcePackage], debian_repository: str
) -> List[SourcePackage]:
    already_published_packages = sync_list_repository_packages(debian_repository)
    filtered_packages: List[SourcePackage] = []
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
    blueprints: List[Blueprint],
    output_directory: Path,
    debian_repository: Optional[str] = None,
) -> None:
    # each blueprint can yield multiple source packages, one per supported arch
    packages = [SourcePackage(b, output_directory) for b in extend(blueprints)]

    # filter out packages already available in the debian repository
    if debian_repository is not None:
        packages = filter_already_published_packages(packages, debian_repository)

    # run fetch instructions (download, verify, extract) in parallel
    files = [p.remote_file for p in packages if p.remote_file is not None]
    fetch_results = Fetcher(files).sync_fetch()

    for package in packages:
        package.generate(fetch_results)

    _, errors = separate_successes_from_errors(fetch_results.values())
    if errors:
        raise Ops2debGeneratorError(f"{len(errors)} failures occurred")
