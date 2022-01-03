import asyncio
import re
from pathlib import Path
from typing import Any, Dict, Optional

from . import logger
from .exceptions import Ops2debBuilderError
from .utils import log_and_raise


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


async def build_package(cwd: Path) -> None:
    """Run dpkg-buildpackage in specified path."""
    args = ["-us", "-uc"]
    arch = parse_debian_control(cwd)["Architecture"]
    if arch != "all":
        args += ["--host-arch", arch]

    logger.info(f"Building {cwd}...")

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
    else:
        logger.info(f"Successfully built {str(cwd)}")


def build(output_directory: Path, workers: int = 4) -> None:
    """
    Run several instances of dpkg-buildpackage in parallel.
    :param output_directory: path where to search for source packages
    :param workers: Number of threads to run in parallel
    """

    logger.title("Building source packages...")

    paths = []
    for output_directory in output_directory.iterdir():
        if output_directory.is_dir() and (output_directory / "debian/control").is_file():
            paths.append(output_directory)

    async def _build_package(sem: asyncio.Semaphore, _path: Path) -> Optional[int]:
        async with sem:  # semaphore limits num of simultaneous builds
            return await build_package(_path)

    async def _build_all() -> Any:
        sem = asyncio.Semaphore(workers)
        return await asyncio.gather(
            *[_build_package(sem, p) for p in paths], return_exceptions=True
        )

    results = asyncio.run(_build_all())

    if errors := [e for e in results if isinstance(e, Exception)]:
        raise Ops2debBuilderError(f"{len(errors)} failures occurred")
