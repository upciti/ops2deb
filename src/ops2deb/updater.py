import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, cast

import httpx
import ruamel.yaml
from ruamel.yaml.emitter import Emitter
from semver.version import Version

from . import logger
from .client import client_factory
from .exceptions import Ops2debUpdaterError
from .fetcher import Fetcher, FetchResult, FetchResultOrError
from .parser import Blueprint, RemoteFile, extend, load, validate
from .utils import separate_successes_from_errors


# fixme: move this somewhere else, this code is also duplicated in formatter.py
class FixIndentEmitter(Emitter):
    def expect_block_sequence(self) -> None:
        self.increase_indent(flow=False, indentless=False)
        self.state = self.expect_first_block_sequence_item


@dataclass(frozen=True)
class LatestRelease:
    blueprint: Blueprint
    version: str

    @property
    def is_new(self) -> bool:
        return self.blueprint.version != self.version


class BaseUpdateStrategy:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def _try_version(self, blueprint: Blueprint, version: str) -> bool:
        if not (remote_file := blueprint.render_fetch(version=version)):
            return False
        url = remote_file.url
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
    ) -> Optional[Version]:
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
        if (fetch := blueprint.render_fetch()) is None:
            raise ValueError(f"Blueprint {blueprint.name} has no fetch instruction")
        if (match := re.match(cls.github_url_re, fetch.url)) is None:
            raise ValueError(f"URL {fetch.url} is not supported")
        return f"{cls.github_base_api_url}/repos/{match['owner']}/{match['name']}"

    async def _get_latest_github_release(self, blueprint: Blueprint) -> Dict[str, Any]:
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
        return response.json()

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


async def _find_latest_version(client: httpx.AsyncClient, blueprint: Blueprint) -> str:
    strategies = [GithubUpdateStrategy(client), GenericUpdateStrategy(client)]
    strategies = [u for u in strategies if u.is_blueprint_supported(blueprint)]
    if not strategies:
        return blueprint.version
    for update_strategy in strategies:
        try:
            return await update_strategy(blueprint)
        except Ops2debUpdaterError as e:
            logger.debug(
                f"{update_strategy.__class__.__name__} - {blueprint.name} - {str(e)}"
            )
            continue
    error = f"Failed to update {blueprint.name}, enable debug logs for more information"
    logger.error(error)
    raise Ops2debUpdaterError(error)


async def _find_latest_release(
    client: httpx.AsyncClient, blueprint: Blueprint
) -> LatestRelease:
    version = await _find_latest_version(client, blueprint)
    if blueprint.version != version:
        logger.info(
            f"{blueprint.name} can be bumped from {blueprint.version} to {version}"
        )
    return LatestRelease(blueprint, version)


def _find_latest_releases(
    blueprints: List[Blueprint],
) -> List[Union[LatestRelease, Exception]]:
    async def run_tasks() -> Any:
        async with client_factory() as client:
            tasks = [_find_latest_release(client, blueprint) for blueprint in blueprints]
            return await asyncio.gather(*tasks, return_exceptions=True)

    return asyncio.run(run_tasks())


def _fetch_latest_files(
    latest_releases: List[LatestRelease],
) -> Dict[str, FetchResultOrError]:
    blueprints = [
        release.blueprint.copy(update={"version": release.version})
        for release in latest_releases
        if release.blueprint.version != release.version
    ]
    remote_files = [cast(RemoteFile, b.render_fetch()) for b in extend(blueprints)]
    fetcher = Fetcher(remote_files)
    return fetcher.sync_fetch(extract=False)


def _update_raw_blueprint(
    raw_blueprint: Dict[str, Any],
    latest_release: LatestRelease,
    fetch_results: Dict[str, FetchResultOrError],
) -> None:
    new_sha256_object: Any = {}

    for arch in latest_release.blueprint.supported_architectures():
        blueprint = latest_release.blueprint.copy(update={"arch": arch})
        remote_file = cast(RemoteFile, blueprint.render_fetch(latest_release.version))
        fetch_result = fetch_results[remote_file.url]
        if not isinstance(fetch_result, FetchResult):
            return
        if isinstance(latest_release.blueprint.fetch, RemoteFile):
            new_sha256_object = fetch_result.sha256_sum
        else:
            new_sha256_object[arch] = fetch_result.sha256_sum

    raw_blueprint["fetch"]["sha256"] = new_sha256_object
    raw_blueprint["version"] = latest_release.version
    raw_blueprint.pop("revision", None)


def update(
    configuration_path: Path, dry_run: bool = False, output_path: Optional[Path] = None
) -> None:
    yaml = ruamel.yaml.YAML(typ="rt")
    yaml.Emitter = FixIndentEmitter

    configuration_dict = load(configuration_path, yaml)
    blueprints = validate(configuration_dict)

    logger.title("Looking for new releases...")

    updater_results = _find_latest_releases(blueprints)
    latest_releases, updater_errors = separate_successes_from_errors(updater_results)
    latest_releases = [release for release in latest_releases if release.is_new]
    fetch_results = _fetch_latest_files(latest_releases)

    # configuration file can be a list of blueprints or a single blueprint
    raw_blueprints = (
        configuration_dict
        if isinstance(configuration_dict, list)
        else [configuration_dict]
    )

    if not latest_releases:
        logger.info("Did not found any updates")

    if dry_run is False and latest_releases:
        for raw_blueprint, release in zip(raw_blueprints, updater_results):
            if isinstance(release, LatestRelease) and release.is_new:
                _update_raw_blueprint(raw_blueprint, release, fetch_results)
        with configuration_path.open("w") as output:
            yaml.dump(configuration_dict, output)

        logger.info("Configuration file updated")

    if output_path is not None:
        lines = [
            f"Updated {r.blueprint.name} from {r.blueprint.version} to {r.version}"
            for r in latest_releases
        ]
        output_path.write_text("\n".join(lines + [""]))

    _, fetcher_errors = separate_successes_from_errors(fetch_results.values())
    if fetcher_errors or updater_errors:
        raise Ops2debUpdaterError(
            f"{len(fetcher_errors)+len(updater_errors)} failures occurred"
        )
