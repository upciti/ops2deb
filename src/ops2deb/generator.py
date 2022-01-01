import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from jinja2 import Environment, FunctionLoader

from . import logger
from .apt import DebianRepositoryPackage, sync_list_repository_packages
from .exceptions import Ops2debGeneratorError, Ops2debGeneratorScriptError
from .fetcher import Fetcher, FetchResult, FetchResultOrError
from .parser import Blueprint
from .templates import template_loader
from .utils import split_successes_from_errors

_environment = Environment(loader=FunctionLoader(template_loader))


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
        self.blueprint = blueprint.render(self.source_directory)

    def _render_template(self, template_name: str) -> None:
        template = _environment.get_template(f"{template_name}")
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
        for line in self.blueprint.script:
            logger.info(f"$ {line}")
            result = subprocess.run(line, shell=True, cwd=cwd, capture_output=True)
            if stdout := result.stdout.decode():
                logger.info(_format_command_output(stdout))
            if stderr := result.stderr.decode():
                logger.error(_format_command_output(stderr))
            if result.returncode:
                raise Ops2debGeneratorScriptError

    def generate(self, fetcher: Fetcher) -> None:
        fetch_result: Optional[FetchResultOrError] = None
        if self.blueprint.fetch is not None:
            fetch_result = fetcher.results[self.blueprint.fetch.url]
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
    packages = [SourcePackage(blueprint, output_directory) for blueprint in blueprints]

    # filter out blueprints that build packages already available in the debian repository
    if debian_repository is not None:
        packages = filter_already_published_packages(packages, debian_repository)

    # run fetch instructions (download, verify, extract) in parallel
    files = [p.blueprint.fetch for p in packages if p.blueprint.fetch is not None]
    fetcher = Fetcher(files)
    fetcher.sync_fetch()

    for package in packages:
        package.generate(fetcher)

    _, errors = split_successes_from_errors(fetcher.results.values())
    if errors:
        raise Ops2debGeneratorError(f"{len(errors)} failures occurred")
