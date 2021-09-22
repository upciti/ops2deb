import asyncio
import re
from pathlib import Path
from typing import Dict, Optional

import typer

from .settings import settings


def parse_debian_control(cwd: Path) -> Dict[str, str]:
    """
    Extract fields from debian/control
    :param cwd: Path to debian source package
    :return: Dict object with fields as keys
    """
    field_re = re.compile(r"^([\w-]+)\s*:\s*(.+)")

    content = (cwd / "debian" / "control").read_text()
    control = {}
    for line in content.split("\n"):
        m = field_re.search(line)
        if m:
            g = m.groups()
            control[g[0]] = g[1]

    return control


async def build_package(cwd: Path) -> Optional[int]:
    """Run dpkg-buildpackage in specified path."""
    args = ["-us", "-uc"]
    arch = parse_debian_control(cwd)["Architecture"]
    if arch != "all":
        args += ["--host-arch", arch]

    typer.secho(f"* Building {cwd}...", fg=typer.colors.WHITE)

    proc = await asyncio.create_subprocess_exec(
        "/usr/bin/dpkg-buildpackage",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode:
        typer.secho(f"Failed to build package in {str(cwd)}", fg=typer.colors.RED)
    else:
        typer.secho(f"* Successfully built {str(cwd)}", fg=typer.colors.WHITE)
    if settings.verbose:
        if stdout:
            typer.secho(stdout.decode(), fg=typer.colors.BRIGHT_BLACK)
        if stderr:
            typer.secho(stderr.decode(), fg=typer.colors.BRIGHT_BLACK)

    return proc.returncode


def build(path: Path, workers: int = 4) -> None:
    """
    Run several instances of dpkg-buildpackage in parallel.
    :param path: path where to search for source packages
    :param workers: Number of threads to run in parallel
    """

    typer.secho("Building source packages...", fg=typer.colors.BLUE, bold=True)

    paths = []
    for path in path.iterdir():
        if path.is_dir() and (path / "debian/control").is_file():
            paths.append(path)

    async def _build_package(sem: asyncio.Semaphore, _path: Path) -> Optional[int]:
        async with sem:  # semaphore limits num of simultaneous builds
            return await build_package(_path)

    async def _build_all() -> None:
        sem = asyncio.Semaphore(workers)
        await asyncio.gather(*[_build_package(sem, p) for p in paths])

    asyncio.run(_build_all())
