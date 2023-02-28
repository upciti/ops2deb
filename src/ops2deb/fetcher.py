import asyncio
import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import aiofiles
import httpx

from ops2deb import logger
from ops2deb.client import client_factory
from ops2deb.exceptions import Ops2debError, Ops2debFetcherError
from ops2deb.extracter import extract_archive, is_archive_format_supported
from ops2deb.parser import Parser
from ops2deb.utils import log_and_raise, separate_results_from_errors

DEFAULT_CACHE_DIRECTORY = Path("/tmp/ops2deb_cache")


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
    def __init__(self, cache_directory_path: Path):
        self.cache_directory_path = cache_directory_path

    async def _run_tasks(
        self,
        tasks: Sequence[FetchTask],
    ) -> tuple[dict[str, FetchResult], dict[str, Ops2debError]]:
        if tasks:
            logger.title(f"Fetching {len(tasks)} files...")
        results = await asyncio.gather(
            *[t.fetch(self.cache_directory_path) for t in tasks], return_exceptions=True
        )
        return separate_results_from_errors(dict(zip([r.url for r in tasks], results)))

    def fetch_urls(
        self,
        urls: Sequence[str],
    ) -> tuple[dict[str, FetchResult], dict[str, Ops2debError]]:
        return asyncio.run(self._run_tasks([FetchTask(url) for url in set(urls)]))

    def fetch_urls_and_check_hashes(
        self, tasks: Sequence[FetchTask]
    ) -> tuple[dict[str, FetchResult], dict[str, Ops2debError]]:
        return asyncio.run(self._run_tasks(tasks))

    def update_lockfiles(self, search_glob: str) -> None:
        parser = Parser(search_glob)
        blueprint_urls: dict[int, list[str]] = {}
        all_urls: list[str] = []
        for blueprint in parser.blueprints:
            lock = parser.get_metadata(blueprint).lock
            urls = [url for url in blueprint.render_fetch_urls() if url not in lock]
            if urls:
                blueprint_urls[blueprint.uid] = urls
                all_urls.extend(urls)
        results, fetch_errors = self.fetch_urls(all_urls)
        for url, fetch_result in results.items():
            for uid, urls in blueprint_urls.items():
                if url in urls:
                    lock = parser.metadatas[uid].lock
                    lock.add([fetch_result])
        parser.save()
