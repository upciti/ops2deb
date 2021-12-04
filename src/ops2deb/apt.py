import asyncio
from dataclasses import dataclass
from itertools import chain, product
from typing import Iterator, List

import httpx
from debian.deb822 import Packages, Release
from pydantic import BaseModel, Field, HttpUrl, ValidationError

from . import logger
from .client import client_factory
from .exceptions import AptError


class DebianRepository(BaseModel):
    url: HttpUrl
    distribution: str = Field(..., regex=r"[a-zA-Z0-9]+")


@dataclass
class DebianRepositoryPackage:
    name: str
    version: str
    architecture: str


async def _download_repository_release_file(
    client: httpx.AsyncClient, distribution: str
) -> Release:
    url = f"/dists/{distribution}/Release"
    response = await client.get(url)
    response.raise_for_status()
    return Release(response.text)


async def _download_repository_packages_file(
    client: httpx.AsyncClient, distribution: str, component: str, arch: str
) -> bytes:
    url = f"/dists/{distribution}/{component}/binary-{arch}/Packages"
    logger.debug(f"Downloading {url}...")
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        return await response.aread()


def _parse_repository_packages_file(content: bytes) -> Iterator[DebianRepositoryPackage]:
    """Extract package names and versions from a repo Packages file"""
    for src in Packages.iter_paragraphs(content, use_apt_pkg=False):
        yield DebianRepositoryPackage(src["Package"], src["Version"], src["Architecture"])


def _parse_debian_repository_option(debian_repository: str) -> DebianRepository:
    try:
        url, distribution = debian_repository.split(" ")
    except ValueError:
        raise AptError(
            "The expected format for the --repository option is "
            '"{repository_url} {distribution}"'
        )
    try:
        return DebianRepository(url=url, distribution=distribution)
    except ValidationError as e:
        raise AptError(str(e))


async def list_repository_packages(
    debian_repository: str,
) -> List[DebianRepositoryPackage]:
    repository = _parse_debian_repository_option(debian_repository)
    async with client_factory(base_url=repository.url) as client:
        release = await _download_repository_release_file(client, repository.distribution)
        architectures = release["Architectures"].split(" ")
        components = release["Components"].split(" ")
        logger.info(
            f"Repository {repository.url} has architectures "
            f"{architectures} and components {components}"
        )
        tasks = [
            _download_repository_packages_file(
                client, repository.distribution, component, architecture
            )
            for component, architecture in product(components, architectures)
        ]
        results = [
            _parse_repository_packages_file(r) for r in await asyncio.gather(*tasks)
        ]
    return list(chain(*results))


def sync_list_repository_packages(
    debian_repository: str,
) -> List[DebianRepositoryPackage]:
    """Example: "http://deb.wakemeops.com/ stable" """
    return asyncio.run(list_repository_packages(debian_repository))
