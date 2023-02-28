import asyncio
import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiofiles
import httpx

from ops2deb import logger
from ops2deb.client import client_factory
from ops2deb.exceptions import Ops2debError, Ops2debFetcherError
from ops2deb.extracter import extract_archive, is_archive_format_supported
from ops2deb.utils import log_and_raise

DEFAULT_CACHE_DIRECTORY = Path("/tmp/ops2deb_cache")


@dataclass
class FetchResult:
    url: str
    sha256: str
    storage_path: Path
    task_data: Any


@dataclass(frozen=True)
class FetchFailure:
    url: str
    error: Ops2debError
    task_data: Any


@dataclass
class FetchTask:
    url: str
    task_datas: list[Any]
    expected_sha256: str | None = None


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


class Fetcher:
    def __init__(self, cache_directory_path: Path):
        self._cache_directory_path = cache_directory_path
        self._tasks: dict[str, FetchTask] = {}
        self._results: list[FetchResult] = []
        self._failures: list[FetchFailure] = []

    async def _download_hash_extract(self, task: FetchTask) -> None:
        url_hash = hashlib.sha256(task.url.encode()).hexdigest()
        file_name = task.url.split("/")[-1]
        base_path = self._cache_directory_path / url_hash
        download_path = base_path / file_name
        extract_path = base_path / f"{file_name}_out"
        checksum_path = base_path / f"{file_name}.sum"

        base_path.mkdir(exist_ok=True, parents=True)
        if download_path.is_file() is False:
            await _download_file(task.url, download_path)

        if checksum_path.is_file() is False:
            computed_hash = await _hash_file(download_path)
            checksum_path.write_text(computed_hash)
        else:
            computed_hash = checksum_path.read_text()

        storage_path = (
            extract_path
            if task.expected_sha256 and is_archive_format_supported(download_path)
            else download_path
        )

        if task.expected_sha256:
            if computed_hash != task.expected_sha256:
                log_and_raise(
                    Ops2debFetcherError(
                        f"Wrong checksum for file {file_name}. "
                        f"Expected {task.expected_sha256}, got {computed_hash}."
                    )
                )
            if extract_path.exists() is False and storage_path == extract_path:
                await extract_archive(download_path, extract_path)

        logger.info(f"Done with {download_path.name}")
        for task_data in task.task_datas:
            self._results.append(
                FetchResult(task.url, computed_hash, storage_path, task_data)
            )

    async def _run_task(
        self,
        task: FetchTask,
    ) -> None:
        try:
            await self._download_hash_extract(task)
        except Ops2debError as exception:
            for task_data in task.task_datas:
                self._failures.append(FetchFailure(task.url, exception, task_data))

    async def _run_tasks(self) -> None:
        if self._tasks:
            logger.title(f"Fetching {len(self._tasks)} files...")
        tasks = self._tasks.values()
        await asyncio.gather(*[self._run_task(task) for task in tasks])

    def add_task(self, url: str, *, data: Any, sha256: str | None = None) -> None:
        task = self._tasks.get(url, FetchTask(url, [], sha256))
        task.task_datas.append(data)
        self._tasks[url] = task

    def run_tasks(self) -> tuple[list[FetchResult], list[FetchFailure]]:
        asyncio.run(self._run_tasks())
        results = self._results
        self._results = []
        failures = self._failures
        self._failures = []
        return results, failures
