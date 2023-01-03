import asyncio
import bz2
import gzip
import hashlib
import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import aiofiles
import httpx
import unix_ar

from ops2deb import logger
from ops2deb.client import client_factory
from ops2deb.exceptions import Ops2debError, Ops2debExtractError, Ops2debFetcherError
from ops2deb.lockfile import Lock
from ops2deb.parser import RemoteFile, extend, parse
from ops2deb.utils import log_and_raise, separate_results_from_errors

DEFAULT_CACHE_DIRECTORY = Path("/tmp/ops2deb_cache")


def _unpack_gz(file_path: str, extract_path: str) -> None:
    output_path = Path(extract_path) / Path(file_path).stem
    with output_path.open("wb") as output:
        with gzip.open(file_path, "rb") as gz_archive:
            shutil.copyfileobj(gz_archive, output)


def _unpack_bz2(file_path: str, extract_path: str) -> None:
    output_path = Path(extract_path) / Path(file_path).stem
    with output_path.open(mode="wb") as output:
        with bz2.open(file_path, "rb") as bz2_archive:
            shutil.copyfileobj(bz2_archive, output)


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


shutil.register_unpack_format("gz", [".gz"], _unpack_gz)
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


def is_archive_format_supported(archive_path: Path) -> bool:
    for name, extensions, _ in shutil.get_unpack_formats():
        for extension in extensions:
            if archive_path.name.endswith(extension):
                return True
    return False


async def extract_archive(archive_path: Path, extract_path: Path) -> None:
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


@dataclass
class FetchResult:
    url: str
    sha256: str
    storage_path: Path


class FetchTask:
    def __init__(self, url: str, sha256: str | None = None):
        self.url = url
        self.sha256 = sha256

    async def fetch(self, cache_directory_path: Path) -> FetchResult:
        url_hash = hashlib.sha256(self.url.encode()).hexdigest()
        file_name = self.url.split("/")[-1]
        base_path = cache_directory_path / url_hash
        download_path = base_path / file_name
        extract_path = base_path / f"{file_name}_out"
        checksum_path = base_path / f"{file_name}.sum"

        base_path.mkdir(exist_ok=True, parents=True)
        if download_path.is_file() is False:
            await _download_file(self.url, download_path)

        if checksum_path.is_file() is False:
            computed_hash = await _hash_file(download_path)
            checksum_path.write_text(computed_hash)
        else:
            computed_hash = checksum_path.read_text()

        storage_path = (
            extract_path
            if self.sha256 and is_archive_format_supported(download_path)
            else download_path
        )

        if self.sha256:
            if computed_hash != self.sha256:
                log_and_raise(
                    Ops2debFetcherError(
                        f"Wrong checksum for file {file_name}. "
                        f"Expected {self.sha256}, got {computed_hash}."
                    )
                )
            if extract_path.exists() is False and storage_path == extract_path:
                await extract_archive(download_path, extract_path)

        logger.info(f"Done with {download_path.name}")
        return FetchResult(self.url, computed_hash, storage_path)


class Fetcher:
    def __init__(self, cache_directory_path: Path, lockfile_path: Path):
        self.cache_directory_path = cache_directory_path
        self.lock = Lock(lockfile_path)

    async def _run_tasks(
        self,
        tasks: list[FetchTask],
    ) -> tuple[dict[str, FetchResult], dict[str, Ops2debError]]:
        if tasks:
            logger.title(f"Fetching {len(tasks)} files...")
        results = await asyncio.gather(
            *[t.fetch(self.cache_directory_path) for t in tasks], return_exceptions=True
        )
        return separate_results_from_errors(dict(zip([r.url for r in tasks], results)))

    async def fetch_urls(
        self,
        urls: Sequence[str],
    ) -> tuple[dict[str, FetchResult], dict[str, Ops2debError]]:
        return await self._run_tasks([FetchTask(url) for url in set(urls)])

    def fetch_urls_and_check_hashes(
        self,
        remote_files: Sequence[str | RemoteFile],
    ) -> tuple[dict[str, FetchResult], dict[str, Ops2debError]]:
        tasks: list[FetchTask] = []
        for remote_file in set(remote_files):
            if isinstance(remote_file, str):
                task = FetchTask(remote_file, self.lock.sha256(remote_file))
            else:
                # TODO: For backward compatibility, RemoteFile will soon be removed
                task = FetchTask(remote_file.url, remote_file.sha256)
            tasks.append(task)
        return asyncio.run(self._run_tasks(tasks))

    def update_lockfile(self, configuration_path: Path) -> None:
        blueprints = extend(parse(configuration_path))

        urls: list[str] = []
        for blueprint in blueprints:
            url = blueprint.render_fetch_url()
            if url is not None and url not in self.lock:
                urls.append(url)

        results, fetch_errors = asyncio.run(self.fetch_urls(urls))
        self.lock.add(list(results.values()))
        self.lock.save()
