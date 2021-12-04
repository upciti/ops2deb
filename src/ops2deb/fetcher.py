import asyncio
import hashlib
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

import aiofiles
import httpx

from . import logger
from .client import client_factory
from .exceptions import FetchError

DEFAULT_CACHE_DIRECTORY = Path("/tmp/ops2deb_cache")
_cache_directory = DEFAULT_CACHE_DIRECTORY


def set_cache_directory(path: Path) -> None:
    """Directory in which files are downloaded"""
    global _cache_directory
    _cache_directory = path


def _error(msg: str) -> None:
    logger.error(msg)
    raise FetchError(msg)


async def _run(program: str, *args: str, cwd: Path) -> asyncio.subprocess.Process:
    proc = await asyncio.create_subprocess_exec(
        program,
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    return proc


async def _extract_and_delete_archive(file_path: Path) -> None:
    commands = [
        ({".tar.gz", ".tar.xz", ".tar"}, ["/bin/tar", "xf", str(file_path)]),
        ({".zip"}, ["/bin/unzip", str(file_path)]),
    ]
    selected_command: Optional[List[str]] = None
    for extensions, command in commands:
        for extension in extensions:
            if file_path.name.endswith(extension):
                selected_command = command
                break
    if selected_command is not None:
        logger.info(f"Extracting {file_path.name}...")
        proc = await _run(*selected_command, cwd=file_path.parent)
        if proc.returncode:
            _error(f"Failed to extract archive {file_path.name}")
        else:
            file_path.unlink()


async def _compute_file_checksum(file_path: Path) -> str:
    sha256_hash = hashlib.sha256()
    with file_path.open("rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
            await asyncio.sleep(0)
    return sha256_hash.hexdigest()


async def _download_file(url: str, file_path: str) -> None:
    async with client_factory() as client:
        async with client.stream("GET", url) as r:
            if 400 <= r.status_code < 600:
                _error(
                    f"Failed to download {url}. Server responded with {r.status_code}."
                )
            # FIXME: https://github.com/Tinche/aiofiles/issues/91
            async with aiofiles.open(file_path, "wb") as f:  # type: ignore
                async for chunk in r.aiter_bytes():
                    await f.write(chunk)


async def download_file_to_cache(
    url: str, expected_hash: Optional[str] = None
) -> Tuple[Path, str]:
    _cache_directory.mkdir(exist_ok=True)
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    file_name = url.split("/")[-1]
    file_path = _cache_directory / f"{url_hash}_{file_name}"
    tmp_path = f"{file_path}.part"

    if not file_path.is_file():
        logger.info(f"Downloading {file_name}...")
        try:
            await _download_file(url, tmp_path)
        except httpx.HTTPError as e:
            _error(f"Failed to download {url}. {str(e)}")
        shutil.move(tmp_path, file_path)

    logger.info(f"Computing checksum for {file_name}...")
    computed_hash = await _compute_file_checksum(file_path)

    if expected_hash is not None:
        if computed_hash != expected_hash:
            _error(
                f"Wrong checksum for file {file_name}. "
                f"Expected {expected_hash}, got {computed_hash}."
            )

    return file_path, computed_hash


async def fetch(url: str, expected_hash: str, save_path: Path) -> None:
    file_name = url.split("/")[-1]
    file_path, _ = await download_file_to_cache(url, expected_hash)
    shutil.copy(file_path, save_path / file_name)
    await _extract_and_delete_archive(save_path / file_name)
    logger.info(f"Done with {file_name}")
