import asyncio
import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import aiofiles
import httpx

from . import logger
from .client import client_factory
from .exceptions import Ops2debFetcherError
from .parser import RemoteFile
from .utils import log_and_raise

DEFAULT_CACHE_DIRECTORY = Path("/tmp/ops2deb_cache")


async def _run(*args: str) -> asyncio.subprocess.Process:
    logger.debug(f"Running {' '.join(args)}")
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    return proc


async def _download_file(url: str, download_path: Path) -> None:
    tmp_path = f"{download_path}.part"
    logger.info(f"Downloading {download_path.name}...")
    try:
        async with client_factory() as client:
            async with client.stream("GET", url) as r:
                if 400 <= r.status_code < 600:
                    log_and_raise(
                        Ops2debFetcherError(
                            f"Failed to download {url}. "
                            f"Server responded with {r.status_code}."
                        )
                    )
                async with aiofiles.open(tmp_path, "wb") as f:
                    async for chunk in r.aiter_bytes():
                        await f.write(chunk)
    except httpx.HTTPError as e:
        log_and_raise(Ops2debFetcherError(f"Failed to download {url}. {str(e)}"))
    shutil.move(tmp_path, download_path)


async def _hash_file(file_path: Path) -> str:
    logger.info(f"Computing checksum for {file_path.name}...")
    sha256_hash = hashlib.sha256()
    with file_path.open("rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
            await asyncio.sleep(0)
    return sha256_hash.hexdigest()


async def _extract_archive(archive_path: Path, extract_path: Path) -> bool:
    tmp_extract_path = f"{extract_path}_tmp"
    Path(tmp_extract_path).mkdir(exist_ok=True)
    commands = [
        (
            {".tar.gz", ".tar.xz", ".tar"},
            ["/bin/tar", "-C", tmp_extract_path, "-xf", str(archive_path)],
        ),
        ({".zip"}, ["/usr/bin/unzip", "-d", tmp_extract_path, str(archive_path)]),
    ]
    selected_command: Optional[List[str]] = None

    for extensions, command in commands:
        for extension in extensions:
            if archive_path.name.endswith(extension):
                selected_command = command
                break

    if selected_command is None:
        return False

    logger.info(f"Extracting {archive_path.name}...")
    proc = await _run(*selected_command)
    if proc.returncode:
        log_and_raise(Ops2debFetcherError(f"Failed to extract archive {archive_path}"))

    shutil.move(tmp_extract_path, extract_path)
    return True


@dataclass(frozen=True)
class FetchResult:
    sha256_sum: str
    storage_path: Path


@dataclass(frozen=True)
class FetchResults:
    successes: Dict[str, FetchResult] = field(default_factory=dict)
    errors: Dict[str, Ops2debFetcherError] = field(default_factory=dict)


class FetchTask:
    def __init__(self, cache_directory_path: Path, remote_file: RemoteFile):
        url_hash = hashlib.sha256(remote_file.url.encode()).hexdigest()
        self.url = remote_file.url
        self.file_name = remote_file.url.split("/")[-1]
        self.base_path = cache_directory_path / url_hash
        self.download_path = self.base_path / self.file_name
        self.extract_path = self.base_path / f"{self.file_name}_out"
        self.checksum_path = self.base_path / f"{self.file_name}.sum"
        self.expected_hash = remote_file.sha256

    async def fetch(self, extract: bool) -> FetchResult:
        self.base_path.mkdir(exist_ok=True, parents=True)
        storage_path = self.download_path
        if self.download_path.is_file() is False:
            await _download_file(self.url, self.download_path)

        if self.checksum_path.is_file() is False:
            computed_hash = await _hash_file(self.download_path)
            self.checksum_path.write_text(computed_hash)
        else:
            computed_hash = self.checksum_path.read_text()

        if extract is True:
            if computed_hash != self.expected_hash:
                log_and_raise(
                    Ops2debFetcherError(
                        f"Wrong checksum for file {self.file_name}. "
                        f"Expected {self.expected_hash}, got {computed_hash}."
                    )
                )
            if self.extract_path.exists() is False:
                if await _extract_archive(self.download_path, self.extract_path) is True:
                    storage_path = self.extract_path
            else:
                storage_path = self.extract_path
        logger.info(f"Done with {self.download_path.name}")
        return FetchResult(computed_hash, storage_path)


class Fetcher:
    cache_directory_path = DEFAULT_CACHE_DIRECTORY

    @classmethod
    def set_cache_directory(cls, path: Path) -> None:
        cls.cache_directory_path = path

    def __init__(self, remote_files: Iterable[RemoteFile]) -> None:
        self.fetch_tasks: Dict[str, FetchTask] = {}
        for remote_file in remote_files:
            self.fetch_tasks[remote_file.url] = FetchTask(
                self.cache_directory_path, remote_file
            )

    async def fetch(self, extract: bool = True) -> FetchResults:
        logger.title(f"Fetching {len(self.fetch_tasks.keys())} files...")
        task_results = await asyncio.gather(
            *[task.fetch(extract) for task in self.fetch_tasks.values()],
            return_exceptions=True,
        )
        results = FetchResults()
        for url, task_result in zip(self.fetch_tasks.keys(), task_results):
            if isinstance(task_result, FetchResult):
                results.successes[url] = task_result
            elif isinstance(task_result, Ops2debFetcherError):
                results.errors[url] = task_result
            else:
                raise task_result
        return results

    def sync_fetch(self, extract: bool = True) -> FetchResults:
        results = asyncio.run(self.fetch(extract))
        return results
