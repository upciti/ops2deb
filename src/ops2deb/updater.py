import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, cast

import httpx
import ruamel.yaml
from ruamel.yaml.emitter import Emitter
from semver.version import Version

from . import logger
from .client import client_factory
from .exceptions import Ops2debError, Ops2debUpdaterError
from .fetcher import Fetcher, FetchResult
from .parser import Blueprint, RemoteFile, load, validate
from .utils import separate_results_from_errors


# fixme: move this somewhere else, this code is also duplicated in formatter.py
class FixIndentEmitter(Emitter):
    def expect_block_sequence(self) -> None:
        self.increase_indent(flow=False, indentless=False)
        self.state = self.expect_first_block_sequence_item


@dataclass(frozen=True)
class LatestRelease:
    blueprint: Blueprint
    version: str
    remote_files: Dict[str, RemoteFile]

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
) -> Optional[LatestRelease]:
    version = await _find_latest_version(client, blueprint)
    if blueprint.version == version:
        return None

    remote_files = {}
    for arch in blueprint.supported_architectures():
        blueprint = blueprint.copy(update={"arch": arch})
        remote_file = cast(RemoteFile, blueprint.render_fetch(version))
        remote_files[arch] = remote_file

    logger.info(f"{blueprint.name} can be bumped from {blueprint.version} to {version}")
    return LatestRelease(blueprint, version, remote_files)


async def _find_latest_releases(
    blueprint_list: List[Blueprint], skip_names: List[str]
) -> Tuple[Dict[int, LatestRelease], Dict[int, Ops2debError]]:
    async with client_factory() as client:
        blueprints = {
            index: blueprint
            for index, blueprint in enumerate(blueprint_list)
            if blueprint.name not in skip_names and blueprint.fetch is not None
        }
        tasks = [_find_latest_release(client, b) for b in blueprints.values()]
        tasks_results = await asyncio.gather(*tasks, return_exceptions=True)
        results, errors = separate_results_from_errors(
            dict(zip(blueprints.keys(), tasks_results))
        )
        results = {k: r for k, r in results.items() if r is not None}
        return results, errors


def find_latest_releases(
    blueprints: List[Blueprint], skip_names: Optional[List[str]] = None
) -> Tuple[Dict[int, LatestRelease], Dict[int, Ops2debError]]:
    skip_names = skip_names or []
    return asyncio.run(_find_latest_releases(blueprints, skip_names))


def fetch_latest_releases(
    latest_releases: Dict[int, LatestRelease],
) -> Tuple[Dict[str, FetchResult], Dict[str, Ops2debError]]:
    remote_files: List[RemoteFile] = []
    for latest_release in latest_releases.values():
        remote_files.extend(latest_release.remote_files.values())
    fetcher = Fetcher(remote_files)
    return fetcher.sync_fetch(extract=False)


def update_raw_blueprint(
    raw_blueprint: Dict[str, Any],
    latest_release: LatestRelease,
    fetch_results: Dict[str, FetchResult],
) -> None:
    new_sha256_object: Any = {}

    for arch, remote_file in latest_release.remote_files.items():
        fetch_result = fetch_results[remote_file.url]
        if isinstance(latest_release.blueprint.fetch, RemoteFile):
            new_sha256_object = fetch_result.sha256_sum
        else:
            new_sha256_object[arch] = fetch_result.sha256_sum

    raw_blueprint["fetch"]["sha256"] = new_sha256_object
    raw_blueprint["version"] = latest_release.version
    raw_blueprint.pop("revision", None)


def update(
    configuration_path: Path,
    dry_run: bool = False,
    output_path: Optional[Path] = None,
    skip_names: List[str] = None,
) -> None:
    yaml = ruamel.yaml.YAML(typ="rt")
    yaml.Emitter = FixIndentEmitter

    configuration_dict = load(configuration_path, yaml)
    blueprints = validate(configuration_dict)

    logger.title("Looking for new releases...")

    releases, update_errors = find_latest_releases(blueprints, skip_names)
    fetch_results, fetch_errors = fetch_latest_releases(releases)

    # configuration file can be a list of blueprints or a single blueprint
    raw_blueprints = (
        configuration_dict
        if isinstance(configuration_dict, list)
        else [configuration_dict]
    )

    if not releases:
        logger.info("Did not found any updates")

    if dry_run is False and releases:
        for index, release in releases.items():
            update_raw_blueprint(raw_blueprints[index], release, fetch_results)
        with configuration_path.open("w") as output:
            yaml.dump(configuration_dict, output)

        logger.info("Configuration file updated")

    if output_path is not None:
        lines = [
            f"Updated {r.blueprint.name} from {r.blueprint.version} to {r.version}"
            for r in releases.values()
        ]
        output_path.write_text("\n".join(lines + [""]))

    if update_errors or fetch_errors:
        raise Ops2debUpdaterError(
            f"{len(fetch_errors)+len(update_errors)} failures occurred"
        )
