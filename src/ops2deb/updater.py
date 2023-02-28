import asyncio
import itertools
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, OrderedDict, Tuple, cast

import httpx
from semver.version import Version

from ops2deb import logger
from ops2deb.client import client_factory
from ops2deb.exceptions import Ops2debError, Ops2debUpdaterError
from ops2deb.fetcher import Fetcher, FetchResult
from ops2deb.lockfile import Lock
from ops2deb.parser import Blueprint, Configuration
from ops2deb.utils import separate_results_from_errors


class BaseUpdateStrategy:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def _try_version(self, blueprint: Blueprint, version: str) -> bool:
        if not (url := blueprint.render_fetch_url(version=version)):
            return False
        logger.debug(f"{self.__class__.__name__} - {blueprint.name} - Trying {url}")
        try:
            response = await self.client.head(url)
        except httpx.HTTPError as e:
            raise Ops2debUpdaterError(f"Failed HEAD request to {url}. {str(e)}")
        status = response.status_code
        if status >= 500:
            raise Ops2debUpdaterError(f"Server error when requesting {url}")
        elif status >= 400:
            return False
        return True

    @classmethod
    def is_blueprint_supported(cls, blueprint: Blueprint) -> bool:
        raise NotImplementedError

    async def __call__(self, blueprint: Blueprint) -> str:
        raise NotImplementedError


class GenericUpdateStrategy(BaseUpdateStrategy):
    """
    Tries a few blueprint fetch URLs with bumped versions to see if servers
    replies with something else than a 404. More or less a brute force approach.
    """

    async def _try_a_few_patches(
        self, blueprint: Blueprint, version: Version
    ) -> Version | None:
        for i in range(0, 3):
            version = version.bump_patch()
            if await self._try_version(blueprint, str(version)) is True:
                return version
        return None

    async def _try_versions(
        self,
        blueprint: Blueprint,
        version: Version,
        version_part: str,
    ) -> Version:
        bumped_version = getattr(version, f"bump_{version_part}")()
        if await self._try_version(blueprint, str(bumped_version)) is False:
            if version_part != "patch":
                if (
                    result := await self._try_a_few_patches(blueprint, bumped_version)
                ) is not None:
                    return await self._try_versions(blueprint, result, version_part)
                else:
                    return version
            else:
                return version
        else:
            return await self._try_versions(blueprint, bumped_version, version_part)

    @classmethod
    def is_blueprint_supported(cls, blueprint: Blueprint) -> bool:
        if not Version.isvalid(blueprint.version):
            logger.warning(f"{blueprint.name} is not using semantic versioning")
            return False
        return True

    async def __call__(self, blueprint: Blueprint) -> str:
        current_version = version = Version.parse(blueprint.version)
        for version_part in ["minor", "patch"]:
            version = await self._try_versions(blueprint, version, version_part)
        if version == current_version:
            version = await self._try_versions(blueprint, version, "major")
        return str(version)


class GithubUpdateStrategy(BaseUpdateStrategy):
    """Uses Github release API to find the latest release."""

    github_url_re = r"^https://github.com/(?P<owner>[\w-]+)/(?P<name>[\w-]+)/"
    github_media_type = "application/vnd.github.v3+json"
    github_base_api_url = "https://api.github.com"

    @classmethod
    def _get_github_repo_api_base_url(cls, blueprint: Blueprint) -> str:
        if (url := blueprint.render_fetch_url()) is None:
            raise ValueError(f"Blueprint {blueprint.name} has no fetch instruction")
        if (match := re.match(cls.github_url_re, url)) is None:
            raise ValueError(f"URL {url} is not supported")
        return f"{cls.github_base_api_url}/repos/{match['owner']}/{match['name']}"

    async def _get_latest_github_release(self, blueprint: Blueprint) -> dict[str, str]:
        repo_api_base_url = self._get_github_repo_api_base_url(blueprint)
        headers = {"accept": self.github_media_type}
        if (token := os.environ.get("GITHUB_TOKEN")) is not None:
            headers["authorization"] = f"token {token}"
        try:
            response = await self.client.get(
                f"{repo_api_base_url}/releases/latest", headers=headers
            )
        except httpx.HTTPError as e:
            raise Ops2debUpdaterError(f"Failed to request Github API. {e}")
        if response.status_code != 200:
            error = f"Failed to request Github API. Error {response.status_code}."
            try:
                error += f" {response.json()['message']}."
            except Exception:
                pass
            raise Ops2debUpdaterError(error)
        return cast(dict[str, Any], response.json())

    @classmethod
    def is_blueprint_supported(cls, blueprint: Blueprint) -> bool:
        try:
            cls._get_github_repo_api_base_url(blueprint)
            return True
        except ValueError:
            return False

    async def __call__(self, blueprint: Blueprint) -> str:
        latest_release = await self._get_latest_github_release(blueprint)
        if (tag_name := latest_release.get("tag_name")) is None:
            raise Ops2debUpdaterError("Failed to determine latest release version")
        version = tag_name if not tag_name.startswith("v") else tag_name[1:]
        if Version.isvalid(version) and Version.isvalid(blueprint.version):
            version = str(max(Version.parse(version), Version.parse(blueprint.version)))
        if await self._try_version(blueprint, version) is False:
            raise Ops2debUpdaterError("Failed to determine latest release URL")
        return version


@dataclass(frozen=True)
class LatestRelease:
    blueprint: Blueprint
    version: str
    fetched: list[FetchResult]


async def _find_latest_version(client: httpx.AsyncClient, blueprint: Blueprint) -> str:
    strategies = [GithubUpdateStrategy(client), GenericUpdateStrategy(client)]
    strategies = [u for u in strategies if u.is_blueprint_supported(blueprint)]
    if not strategies:
        return blueprint.version
    for update_strategy in strategies:
        try:
            version = await update_strategy(blueprint)
            if version not in blueprint.versions():
                logger.info(
                    f"{blueprint.name} can be bumped "
                    f"from {blueprint.version} to {version}"
                )
            return version
        except Ops2debUpdaterError as e:
            logger.debug(
                f"{update_strategy.__class__.__name__} - {blueprint.name} - {str(e)}"
            )
            continue
    error = f"Failed to update {blueprint.name}, enable debug logs for more information"
    logger.error(error)
    raise Ops2debUpdaterError(error)


def _blueprint_fetch_urls(blueprint: Blueprint, version: str | None = None) -> list[str]:
    urls: list[str] = []
    for architecture in blueprint.architectures():
        urls.append(str(blueprint.render_fetch_url(version, architecture)))
    return urls


async def _find_latest_releases(
    blueprints: list[Blueprint],
    fetcher: Fetcher,
    skip_names: list[str] | None,
    only_names: list[str] | None,
) -> Tuple[list[LatestRelease], list[Ops2debError]]:
    blueprints = [b for b in blueprints if b.fetch is not None]
    if skip_names:
        blueprints = [b for b in blueprints if b.name not in skip_names]
    if only_names:
        blueprints = [b for b in blueprints if b.name in only_names]

    async with client_factory() as client:
        tasks = [_find_latest_version(client, b) for b in blueprints]
        tasks_results = await asyncio.gather(*tasks, return_exceptions=True)
        versions, errors = separate_results_from_errors(
            dict(zip([b.index for b in blueprints], tasks_results))
        )

    # remove blueprints where the current version is still the latest
    blueprints = [b for b in blueprints if versions.get(b.index, b.version) != b.version]

    # gather the urls of files we need to download to get the new checksums
    urls: dict[int, list[str]] = defaultdict(list)
    for blueprint in blueprints:
        urls[blueprint.index] = _blueprint_fetch_urls(
            blueprint, versions[blueprint.index]
        )

    url_list = list(itertools.chain(*[u for u in urls.values()]))
    results, fetch_errors = await fetcher.fetch_urls(url_list)

    # remove blueprint we can't update because we could not fetch associated files
    for failed_url, exception in fetch_errors.items():
        for index, blueprint_urls in urls.items():
            if failed_url in blueprint_urls:
                errors[index] = exception
                blueprints.pop(index)

    latest_releases: list[LatestRelease] = []
    for blueprint in blueprints:
        latest_releases.append(
            LatestRelease(
                blueprint=blueprint,
                version=versions[blueprint.index],
                fetched=[results[url] for url in urls[blueprint.index]],
            )
        )

    return latest_releases, list(fetch_errors.values()) + list(errors.values())


def find_latest_releases(
    blueprints: list[Blueprint],
    fetcher: Fetcher,
    skip_names: list[str] | None,
    only_names: list[str] | None,
) -> Tuple[list[LatestRelease], list[Ops2debError]]:
    return asyncio.run(_find_latest_releases(blueprints, fetcher, skip_names, only_names))


def _update_configuration(
    release: LatestRelease,
    raw_blueprints: list[OrderedDict[str, Any]],
    max_versions: int,
) -> list[str]:
    removed_versions: list[str] = []
    raw_blueprint = raw_blueprints[release.blueprint.index]

    if max_versions == 1:
        if release.blueprint.matrix and release.blueprint.matrix.versions:
            removed_versions = raw_blueprint["matrix"].pop("versions")
        else:
            removed_versions = [raw_blueprint["version"]]
        raw_blueprint["version"] = release.version
        raw_blueprint.pop("revision", None)
        raw_blueprint.move_to_end("version", last=False)
        if "matrix" in raw_blueprint:
            raw_blueprint.move_to_end("matrix", last=False)
        raw_blueprint.move_to_end("name", last=False)
    else:
        if (count := len(release.blueprint.versions())) - max_versions >= 0:
            versions = raw_blueprint["matrix"]["versions"]
            raw_blueprint["matrix"]["versions"] = versions[-(max_versions - 1) :]
            removed_versions = versions[: count - max_versions + 1]
        if "matrix" not in raw_blueprint:
            raw_blueprint["matrix"] = {}
            raw_blueprint.move_to_end("matrix", last=False)
            raw_blueprint.move_to_end("name", last=False)
        if "versions" not in raw_blueprint["matrix"]:
            raw_blueprint["matrix"]["versions"] = [release.blueprint.version]
        raw_blueprint["matrix"]["versions"].append(release.version)
        raw_blueprint.pop("version", None)

    return removed_versions


def _update_lockfile(
    release: LatestRelease, lock: Lock, removed_versions: list[str]
) -> None:
    lock.add(release.fetched)
    for version in removed_versions:
        lock.remove(_blueprint_fetch_urls(release.blueprint, version))


def update(
    configuration: Configuration,
    fetcher: Fetcher,
    dry_run: bool = False,
    output_path: Path | None = None,
    skip_names: list[str] | None = None,
    only_names: list[str] | None = None,
    max_versions: int = 1,
) -> None:
    logger.title("Looking for new releases...")
    blueprints = configuration.blueprints
    releases, errors = find_latest_releases(blueprints, fetcher, skip_names, only_names)

    summary: list[str] = []
    lock = fetcher.lock

    for release in releases:
        removed_versions = _update_configuration(
            release, configuration.raw_blueprints, max_versions
        )
        if max_versions == 1:
            line = (
                f"Updated {release.blueprint.name} from "
                f"{release.blueprint.version} to {release.version}"
            )
        else:
            line = f"Added {release.blueprint.name} v{release.version}"
            if removed_versions:
                line += f" and removed {', '.join([f'v{v}' for v in removed_versions])}"
        _update_lockfile(release, lock, removed_versions)
        summary.append(line)

    if not releases:
        logger.info("Did not found any updates")
    else:
        if dry_run is False:
            configuration.save()
            lock.save()
            logger.info("Lockfile and configuration updated")

    if output_path is not None:
        output_path.write_text("\n".join(summary + [""]))

    if errors:
        raise Ops2debUpdaterError(f"{len(errors)} failures occurred")
