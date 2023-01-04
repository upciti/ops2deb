import asyncio
import itertools
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Tuple, cast

import httpx
import ruamel.yaml
from ruamel.yaml.emitter import Emitter
from semver.version import Version

from ops2deb import logger
from ops2deb.client import client_factory
from ops2deb.exceptions import Ops2debError, Ops2debUpdaterError
from ops2deb.fetcher import Fetcher, FetchResult
from ops2deb.lockfile import Lock
from ops2deb.parser import Blueprint, load, validate
from ops2deb.utils import separate_results_from_errors


# fixme: move this somewhere else, this code is also duplicated in formatter.py
class FixIndentEmitter(Emitter):
    def expect_block_sequence(self) -> None:
        self.increase_indent(flow=False, indentless=False)
        self.state = self.expect_first_block_sequence_item


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
    blueprint_index: int
    blueprint: Blueprint
    version: str
    fetch_results: list[FetchResult]

    def update_configuration(
        self, blueprint_dict: dict[str, Any] | list[dict[str, Any]]
    ) -> None:
        # configuration file can be a list of blueprints or a single blueprint
        raw_blueprint = (
            blueprint_dict[self.blueprint_index]
            if isinstance(blueprint_dict, list)
            else blueprint_dict
        )

        raw_blueprint["version"] = self.version
        raw_blueprint.pop("revision", None)

        # TODO: remove this when fetch.sha256 support is dropped
        if not isinstance(raw_blueprint["fetch"], str):
            raw_blueprint["fetch"].pop("sha256", None)
            if len(architectures := self.blueprint.architectures()) > 1:
                raw_blueprint["matrix"] = {"architectures": architectures}
                raw_blueprint.pop("architecture", None)
                raw_blueprint.pop("arch", None)
            if len(raw_blueprint["fetch"]) == 1:
                raw_blueprint["fetch"] = raw_blueprint["fetch"]["url"]


async def _find_latest_version(client: httpx.AsyncClient, blueprint: Blueprint) -> str:
    strategies = [GithubUpdateStrategy(client), GenericUpdateStrategy(client)]
    strategies = [u for u in strategies if u.is_blueprint_supported(blueprint)]
    if not strategies:
        return blueprint.version
    for update_strategy in strategies:
        try:
            version = await update_strategy(blueprint)
            if version != blueprint.version:
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
        blueprint = blueprint.copy(update={"architecture": architecture})
        urls.append(str(blueprint.render_fetch_url(version)))
    return urls


async def _find_latest_releases(
    blueprint_list: list[Blueprint], fetcher: Fetcher, skip_names: list[str] | None = None
) -> Tuple[list[LatestRelease], dict[int, Ops2debError]]:
    skip_names = skip_names or []
    blueprints = {
        index: blueprint
        for index, blueprint in enumerate(blueprint_list)
        if blueprint.fetch is not None and blueprint.name not in skip_names
    }

    async with client_factory() as client:
        tasks = [_find_latest_version(client, b) for b in blueprints.values()]
        tasks_results = await asyncio.gather(*tasks, return_exceptions=True)
        versions, errors = separate_results_from_errors(
            dict(zip(blueprints.keys(), tasks_results))
        )

    # remove blueprints where the current version is still the latest
    blueprints = {
        i: blueprints[i] for i, v in versions.items() if v != blueprints[i].version
    }

    # gather the urls of files we need to download to get the new checksums
    urls: dict[int, list[str]] = defaultdict(list)
    for index, blueprint in blueprints.items():
        urls[index] = _blueprint_fetch_urls(blueprint, versions[index])

    url_list = list(itertools.chain(*[u for u in urls.values()]))
    results, fetch_errors = await fetcher.fetch_urls(url_list)

    # remove blueprint we can't update because we could not fetch associated files
    for failed_url, exception in fetch_errors.items():
        for index, blueprint_urls in urls.items():
            if failed_url in blueprint_urls:
                errors[index] = exception
    blueprints = {i: b for i, b in blueprints.items() if i not in errors.keys()}

    latest_releases: list[LatestRelease] = []
    for index, blueprint in blueprints.items():
        latest_releases.append(
            LatestRelease(
                blueprint_index=index,
                blueprint=blueprint,
                version=versions[index],
                fetch_results=[results[url] for url in urls[index]],
            )
        )

    return latest_releases, errors


def find_latest_releases(
    blueprint_list: list[Blueprint], fetcher: Fetcher, skip_names: list[str] | None = None
) -> Tuple[list[LatestRelease], dict[int, Ops2debError]]:
    return asyncio.run(_find_latest_releases(blueprint_list, fetcher, skip_names))


def update(
    configuration_path: Path,
    lockfile_path: Path,
    fetcher: Fetcher,
    dry_run: bool = False,
    output_path: Path | None = None,
    skip_names: list[str] | None = None,
) -> None:
    yaml = ruamel.yaml.YAML(typ="rt")
    yaml.Emitter = FixIndentEmitter

    configuration_dict = load(configuration_path, yaml)
    blueprints = validate(configuration_dict)

    logger.title("Looking for new releases...")
    releases, errors = find_latest_releases(blueprints, fetcher, skip_names)
    if not releases:
        logger.info("Did not found any updates")

    if dry_run is False and releases:
        lock = Lock(lockfile_path)
        for release in releases:
            release.update_configuration(configuration_dict)
            lock.add(release.fetch_results)
            lock.remove(_blueprint_fetch_urls(release.blueprint))
        with configuration_path.open("w") as output:
            yaml.dump(configuration_dict, output)
        lock.save()

        logger.info("Lockfile and configuration updated")

    if output_path is not None:
        lines = [
            f"Updated {r.blueprint.name} from {r.blueprint.version} to {r.version}"
            for r in releases
        ]
        output_path.write_text("\n".join(lines + [""]))

    if errors:
        raise Ops2debUpdaterError(f"{len(errors)} failures occurred")
