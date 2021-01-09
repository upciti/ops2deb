import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any, List

import typer
from jinja2 import Environment, PackageLoader

from .fetcher import fetch
from .parser import Blueprint
from .settings import settings

environment = Environment(loader=PackageLoader("ops2deb", "templates"))


class SourcePackage:
    def __init__(self, blueprint: Blueprint):
        self.directory_name = f"{blueprint.name}_{blueprint.version}_{blueprint.arch}"
        self.output_directory = (settings.work_dir / self.directory_name).absolute()
        self.debian_directory = self.output_directory / "debian"
        self.src_directory = self.output_directory / "src"
        self.tmp_directory = Path(f"/tmp/ops2deb_{self.directory_name}")
        self.debian_version = f"{blueprint.version}-{blueprint.revision}~ops2deb"
        self.blueprint = blueprint.render(self.src_directory)

    def render_tpl(self, template_name: str) -> None:
        template = environment.get_template(f"{template_name}.j2")
        package = self.blueprint.dict(exclude={"fetch", "script"})
        package.update({"version": self.debian_version})
        template.stream(package=package).dump(str(self.debian_directory / template_name))

    def init(self) -> None:
        shutil.rmtree(self.debian_directory, ignore_errors=True)
        self.debian_directory.mkdir(parents=True)
        shutil.rmtree(self.tmp_directory, ignore_errors=True)
        self.tmp_directory.mkdir()
        shutil.rmtree(self.src_directory, ignore_errors=True)
        self.src_directory.mkdir(parents=True)
        for path in ["usr/bin", "usr/share", "usr/lib"]:
            (self.src_directory / path).mkdir(parents=True)

    async def fetch(self) -> None:
        if (remote_file := self.blueprint.fetch) is not None:
            await fetch(
                url=remote_file.url,
                expected_hash=remote_file.sha256,
                save_path=self.tmp_directory,
            )

    def generate(self) -> None:
        typer.secho(
            f"Generating source package {self.directory_name}...",
            fg=typer.colors.BLUE,
            bold=True,
        )

        # run script
        for line in self.blueprint.script:
            typer.secho(f"$ {line}", fg=typer.colors.WHITE)
            result = subprocess.run(
                line, shell=True, cwd=self.tmp_directory, capture_output=True
            )
            if stdout := result.stdout.decode():
                typer.secho(stdout.strip(), fg=typer.colors.GREEN)
            if stderr := result.stderr.decode():
                typer.secho(stderr.strip(), fg=typer.colors.RED)
            if result.returncode:
                raise RuntimeError("Script failed")

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


def generate(blueprints: List[Blueprint]) -> None:
    packages = [SourcePackage(b) for b in blueprints]

    # make sure we generate source packages in a clean environment
    # without artifacts from previous builds
    for package in packages:
        package.init()

    # run fetch instructions (download, verify, extract) in parallel
    file_count = sum([1 for b in blueprints if b.fetch is not None])
    typer.secho(f"Fetching {file_count} files...", fg=typer.colors.BLUE, bold=True)

    async def fetch() -> Any:
        return await asyncio.gather(*[p.fetch() for p in packages])

    asyncio.run(fetch())

    # run scripts, build debian/* files
    for package in packages:
        package.generate()
