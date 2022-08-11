import asyncio
import itertools
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union, cast

import httpx
import ruamel.yaml
from ruamel.yaml.emitter import Emitter
from semver.version import Version

from . import logger
from .client import client_factory
from .exceptions import Ops2debError, Ops2debUpdaterError
from .fetcher import FetchResult, fetch_urls
from .parser import Blueprint, RemoteFile, load, validate
from .utils import separate_results_from_errors


# fixme: move this somewhere else, this code is also duplicated in formatter.py
class FixIndentEmitter(Emitter):
    def expect_block_sequence(self) -> None:
        self.increase_indent(flow=False, indentless=False)
        self.state = self.expect_first_block_sequence_item


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


@dataclass(frozen=True)
class LatestRelease:
    blueprint_index: int
    blueprint: Blueprint
    version: str
    fetch_results: Dict[str, FetchResult]

    def update_configuration(
        self, blueprint_dict: Union[Dict[str, Any], List[Dict[str, Any]]]
    ) -> None:
        # configuration file can be a list of blueprints or a single blueprint
        raw_blueprint = (
            blueprint_dict[self.blueprint_index]
            if isinstance(blueprint_dict, list)
            else blueprint_dict
        )

        new_sha256_object: Any = {}
        for arch, fetch_result in self.fetch_results.items():
            if isinstance(self.blueprint.fetch, RemoteFile):
                new_sha256_object = fetch_result.sha256_sum
            else:
                new_sha256_object[arch] = fetch_result.sha256_sum

        raw_blueprint["fetch"]["sha256"] = new_sha256_object
        raw_blueprint["version"] = self.version
        raw_blueprint.pop("revision", None)


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


async def _find_latest_releases(
    blueprint_list: List[Blueprint], skip_names: Optional[List[str]] = None
) -> Tuple[List[LatestRelease], Dict[int, Ops2debError]]:
    skip_names = skip_names or []
    blueprints = {
        index: blueprint
        for index, blueprint in enumerate(blueprint_list)
        if blueprint.fetch is not None and blueprint.name not in skip_names
    }

    async with client_factory() as client:
        tasks = [_find_latest_version(client, b) for b in blueprints.values()]
        tasks_results = await asyncio.gather(*tasks)
        versions, errors = separate_results_from_errors(
            dict(zip(blueprints.keys(), tasks_results))
        )

    # remove blueprints where the current version is still the latest
    blueprints = {i: b for i, b in blueprints.items() if versions[i] != b.version}

    # gather the urls of files we need to download to get the new checksums
    urls: Dict[int, Dict[str, str]] = defaultdict(dict)
    for index, blueprint in blueprints.items():
        for arch in blueprint.supported_architectures():
            blueprint = blueprint.copy(update={"arch": arch})
            remote_file = cast(RemoteFile, blueprint.render_fetch(versions[index]))
            urls[index][arch] = str(remote_file.url)

    url_list = list(itertools.chain(*[u.values() for u in urls.values()]))
    results, fetch_errors = await fetch_urls(url_list)

    # remove blueprint we can't update because we could not fetch associated files
    for failed_url, exception in fetch_errors.items():
        for index, blueprint_urls in urls.items():
            if failed_url in blueprint_urls.values():
                errors[index] = exception
    blueprints = {i: b for i, b in blueprints.items() if i not in errors.keys()}

    latest_releases: List[LatestRelease] = []
    for index, blueprint in blueprints.items():
        latest_releases.append(
            LatestRelease(
                blueprint_index=index,
                blueprint=blueprint,
                version=versions[index],
                fetch_results={arch: results[url] for arch, url in urls[index].items()},
            )
        )

    return latest_releases, errors


def find_latest_releases(
    blueprint_list: List[Blueprint], skip_names: Optional[List[str]] = None
) -> Tuple[List[LatestRelease], Dict[int, Ops2debError]]:
    return asyncio.run(_find_latest_releases(blueprint_list, skip_names))


def update(
    configuration_path: Path,
    dry_run: bool = False,
    output_path: Optional[Path] = None,
    skip_names: Optional[List[str]] = None,
) -> None:
    yaml = ruamel.yaml.YAML(typ="rt")
    yaml.Emitter = FixIndentEmitter

    configuration_dict = load(configuration_path, yaml)
    blueprints = validate(configuration_dict)

    logger.title("Looking for new releases...")
    releases, errors = find_latest_releases(blueprints, skip_names)
    if not releases:
        logger.info("Did not found any updates")

    if dry_run is False and releases:
        for release in releases:
            release.update_configuration(configuration_dict)
        with configuration_path.open("w") as output:
            yaml.dump(configuration_dict, output)

        logger.info("Configuration file updated")

    if output_path is not None:
        lines = [
            f"Updated {r.blueprint.name} from {r.blueprint.version} to {r.version}"
            for r in releases
        ]
        output_path.write_text("\n".join(lines + [""]))

    if errors:
        raise Ops2debUpdaterError(f"{len(errors)} failures occurred")
