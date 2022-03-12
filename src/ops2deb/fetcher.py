import asyncio
import bz2
import hashlib
import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Tuple

import aiofiles
import httpx
import unix_ar

from . import logger
from .client import client_factory
from .exceptions import Ops2debError, Ops2debExtractError, Ops2debFetcherError
from .parser import RemoteFile
from .utils import log_and_raise, separate_results_from_errors

DEFAULT_CACHE_DIRECTORY = Path("/tmp/ops2deb_cache")


def _unpack_bz2(file_path: str, extract_path: str) -> None:
    output_path = Path(extract_path) / Path(file_path).stem
    with output_path.open(mode="wb") as output:
        with bz2.open(file_path, "r") as bz2_archive:
            while chunk := bz2_archive.read(10000):
                output.write(chunk)


def _unpack_deb(file_path: str, extract_path: str) -> None:
    ar_file = unix_ar.open(file_path)
    file_names = [info.name.decode("utf-8") for info in ar_file.infolist()]
    for file_name in file_names:
        if file_name.startswith("debian-binary"):
            continue
        tarball = ar_file.open(file_name)
        tar_file = tarfile.open(fileobj=tarball)
        try:
            tar_file.extractall(Path(extract_path) / file_name.split(".")[0])
        finally:
            tar_file.close()


shutil.register_unpack_format("bz2", [".bz2"], _unpack_bz2)
shutil.register_unpack_format("deb", [".deb"], _unpack_deb)


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


def _is_archive_format_supported(archive_path: Path) -> bool:
    for name, extensions, _ in shutil.get_unpack_formats():
        for extension in extensions:
            if archive_path.name.endswith(extension):
                return True
    return False


async def _extract_archive(archive_path: Path, extract_path: Path) -> None:
    tmp_extract_path = f"{extract_path}_tmp"
    Path(tmp_extract_path).mkdir(exist_ok=True)
    logger.info(f"Extracting {archive_path.name}...")

    try:
        await asyncio.get_running_loop().run_in_executor(
            None, shutil.unpack_archive, archive_path, tmp_extract_path
        )
    except Exception as e:
        error = f"Failed to extract archive {archive_path}"
        if str(e):
            error += f" ({e})"
        log_and_raise(Ops2debExtractError(error))

    shutil.move(tmp_extract_path, extract_path)


@dataclass(frozen=True)
class FetchResult:
    sha256_sum: str
    storage_path: Path


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
        if self.download_path.is_file() is False:
            await _download_file(self.url, self.download_path)

        if self.checksum_path.is_file() is False:
            computed_hash = await _hash_file(self.download_path)
            self.checksum_path.write_text(computed_hash)
        else:
            computed_hash = self.checksum_path.read_text()

        storage_path = (
            self.extract_path
            if extract and _is_archive_format_supported(self.download_path)
            else self.download_path
        )

        if extract is True:
            if computed_hash != self.expected_hash:
                log_and_raise(
                    Ops2debFetcherError(
                        f"Wrong checksum for file {self.file_name}. "
                        f"Expected {self.expected_hash}, got {computed_hash}."
                    )
                )
            if self.extract_path.exists() is False and storage_path == self.extract_path:
                await _extract_archive(self.download_path, self.extract_path)

        logger.info(f"Done with {self.download_path.name}")
        return FetchResult(computed_hash, storage_path)


class Fetcher:
    cache_directory_path = DEFAULT_CACHE_DIRECTORY

    @classmethod
    def set_cache_directory(cls, path: Path) -> None:
        cls.cache_directory_path = path

    def __init__(self, remote_files: Iterable[RemoteFile]) -> None:
        self.tasks: Dict[str, FetchTask] = {}
        for remote_file in remote_files:
            self.tasks[remote_file.url] = FetchTask(
                self.cache_directory_path, remote_file
            )

    async def fetch(
        self, extract: bool = True
    ) -> Tuple[Dict[str, FetchResult], Dict[str, Ops2debError]]:
        urls = self.tasks.keys()
        if (task_count := len(urls)) > 0:
            logger.title(f"Fetching {task_count} files...")
        results = await asyncio.gather(
            *[task.fetch(extract) for task in self.tasks.values()],
            return_exceptions=True,
        )
        return separate_results_from_errors(dict(zip(urls, results)))

    def sync_fetch(
        self, extract: bool = True
    ) -> Tuple[Dict[str, FetchResult], Dict[str, Ops2debError]]:
        return asyncio.run(self.fetch(extract))
