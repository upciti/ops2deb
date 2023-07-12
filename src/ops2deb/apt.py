import asyncio
from dataclasses import dataclass
from itertools import chain, product
from typing import Iterator

import httpx
from debian.deb822 import Packages, Release
from pydantic import BaseModel, HttpUrl, TypeAdapter, ValidationError

from ops2deb import logger
from ops2deb.client import client_factory
from ops2deb.exceptions import Ops2debAptError


@dataclass(frozen=True)
class DebianRepository(BaseModel):
    url: str
    distribution: str


@dataclass(frozen=True, order=True)
class DebianRepositoryPackage:
    name: str
    version: str
    architecture: str


async def _download_repository_release_file(
    client: httpx.AsyncClient, distribution: str
) -> Release:
    url = f"/dists/{distribution}/Release"
    try:
        response = await client.get(url)
        response.raise_for_status()
    except httpx.HTTPError:
        raise Ops2debAptError(f"Failed to download APT repository file at {url}")
    return Release(response.text)


async def _download_repository_packages_file(
    client: httpx.AsyncClient, distribution: str, component: str, architecture: str
) -> bytes:
    url = f"/dists/{distribution}/{component}/binary-{architecture}/Packages"
    logger.debug(f"Downloading {url}...")
    try:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            return await response.aread()
    except httpx.HTTPError:
        raise Ops2debAptError(f"Failed to download APT repository file at {url}")


def _parse_repository_packages_file(content: bytes) -> Iterator[DebianRepositoryPackage]:
    """Extract package names and versions from a repo Packages file"""
    for src in Packages.iter_paragraphs(content, use_apt_pkg=False):
        yield DebianRepositoryPackage(src["Package"], src["Version"], src["Architecture"])


def _parse_debian_repository_option(debian_repository: str) -> DebianRepository:
    try:
        url, distribution = debian_repository.split(" ")
    except ValueError:
        raise Ops2debAptError(
            "The expected format for the --repository option is "
            '"{repository_url} {distribution}"'
        )
    try:
        TypeAdapter(HttpUrl).validate_python(url)
    except ValidationError:
        raise Ops2debAptError("Invalid repository URL")
    return DebianRepository(url=url, distribution=distribution)


async def _list_repository_packages(
    debian_repository: str,
) -> list[DebianRepositoryPackage]:
    repository = _parse_debian_repository_option(debian_repository)
    async with client_factory(base_url=repository.url) as client:
        release = await _download_repository_release_file(client, repository.distribution)
        architectures = release["Architectures"].split(" ")
        components = release["Components"].split(" ")
        logger.debug(
            f"Repository {repository.url} {repository.distribution} has architectures "
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


def list_repository_packages(
    debian_repository: str,
) -> list[DebianRepositoryPackage]:
    """Example: "http://deb.wakemeops.com/ stable" """
    return asyncio.run(_list_repository_packages(debian_repository))
