import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any, List, Optional

from jinja2 import Environment, PackageLoader

from . import logger
from .apt import DebianRepositoryPackage, sync_list_repository_packages
from .exceptions import FetchError, GenerateError, GenerateScriptError
from .fetcher import fetch
from .parser import Blueprint

_environment = Environment(loader=PackageLoader("ops2deb", "templates"))


def _format_command_output(output: str) -> str:
    lines = output.splitlines()
    output = "\n  ".join([line for line in lines])
    return "> " + output


class SourcePackage:
    def __init__(self, blueprint: Blueprint, output_directory: Path):
        self.directory_name = f"{blueprint.name}_{blueprint.version}_{blueprint.arch}"
        self.package_directory = (output_directory / self.directory_name).absolute()
        self.debian_directory = self.package_directory / "debian"
        self.src_directory = self.package_directory / "src"
        self.fetch_directory = Path(f"/tmp/ops2deb_{self.directory_name}")
        self.blueprint = blueprint.render(self.src_directory)
        self.debian_version = (
            f"{self.blueprint.version}-{self.blueprint.revision}~ops2deb"
        )

    def render_tpl(self, template_name: str) -> None:
        template = _environment.get_template(f"{template_name}.j2")
        package = self.blueprint.dict(exclude={"fetch", "script"})
        package.update({"version": self.debian_version})
        template.stream(package=package).dump(str(self.debian_directory / template_name))

    def init(self) -> None:
        shutil.rmtree(self.debian_directory, ignore_errors=True)
        self.debian_directory.mkdir(parents=True)
        shutil.rmtree(self.fetch_directory, ignore_errors=True)
        self.fetch_directory.mkdir()
        shutil.rmtree(self.src_directory, ignore_errors=True)
        self.src_directory.mkdir(parents=True)
        for path in ["usr/bin", "usr/share", "usr/lib"]:
            (self.src_directory / path).mkdir(parents=True)

    async def fetch(self) -> "SourcePackage":
        if (remote_file := self.blueprint.fetch) is not None:
            await fetch(
                url=remote_file.url,
                expected_hash=remote_file.sha256,
                save_path=self.fetch_directory,
            )
        return self

    def generate(self) -> None:
        logger.title(f"Generating source package {self.directory_name}...")

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
                raise GenerateScriptError

        # render debian/* files
        for template in [
            "changelog",
            "control",
            "rules",
            "compat",
            "install",
            "lintian-overrides",
        ]:
            self.render_tpl(template)


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

    # make sure we generate source packages in a clean environment
    # without artifacts from previous builds
    for package in packages:
        package.init()

    # run fetch instructions (download, verify, extract) in parallel
    file_count = sum([1 for b in blueprints if b.fetch is not None])
    logger.title(f"Fetching {file_count} files...")

    async def fetch_all() -> Any:
        return await asyncio.gather(
            *[p.fetch() for p in packages], return_exceptions=True
        )

    results = asyncio.run(fetch_all())

    errors = [e for e in results if isinstance(e, Exception)]
    for error in errors:
        if not isinstance(error, FetchError):
            raise error

    # run scripts, build debian/* files
    packages = [p for p in results if isinstance(p, SourcePackage)]
    for package in packages:
        package.generate()

    if errors:
        raise GenerateError(f"{len(errors)} failures occurred")
