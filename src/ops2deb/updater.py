import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Tuple, cast

import httpx
from semver.version import Version

from ops2deb import logger
from ops2deb.client import client_factory
from ops2deb.exceptions import Ops2debError, Ops2debUpdaterError
from ops2deb.fetcher import Fetcher
from ops2deb.parser import Blueprint, Resources


class BaseUpdateStrategy:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def try_version(self, blueprint: Blueprint, version: str) -> bool:
        # No need to waste an HTTP call when called with the current blueprint version
        if blueprint.version == version:
            return True

        # Fetch url does not depend on blueprint version or blueprint has no fetch
        url = blueprint.render_fetch_url(version=version)
        if url == blueprint.render_fetch_url() or url is None:
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
            if await self.try_version(blueprint, str(version)) is True:
                return version
        return None

    async def _try_versions(
        self,
        blueprint: Blueprint,
        version: Version,
        version_part: str,
    ) -> Version:
        bumped_version = getattr(version, f"bump_{version_part}")()
        if await self.try_version(blueprint, str(bumped_version)) is False:
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
        if not Version.is_valid(blueprint.version):
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
    github_token_env_variable = "OPS2DEB_GITHUB_TOKEN"

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
        token = os.environ.get(GithubUpdateStrategy.github_token_env_variable)
        if token is not None:
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
        if Version.is_valid(version) and Version.is_valid(blueprint.version):
            version = str(max(Version.parse(version), Version.parse(blueprint.version)))
        if await self.try_version(blueprint, version) is False:
            raise Ops2debUpdaterError(
                f"Failed to determine latest release URL (latest tag is {tag_name})"
            )
        return version


@dataclass(frozen=True)
class LatestRelease:
    blueprint: Blueprint
    version: str


async def _find_latest_version(
    client: httpx.AsyncClient, blueprint: Blueprint
) -> LatestRelease | None:
    strategies = [GithubUpdateStrategy(client), GenericUpdateStrategy(client)]
    strategies = [u for u in strategies if u.is_blueprint_supported(blueprint)]
    if not strategies:
        return None
    for update_strategy in strategies:
        try:
            version = await update_strategy(blueprint)
            if version in blueprint.versions():
                return None
            logger.info(
                f"{blueprint.name} can be bumped "
                f"from {blueprint.version} to {version}"
            )
            return LatestRelease(blueprint, version)
        except Ops2debUpdaterError as e:
            logger.debug(
                f"{update_strategy.__class__.__name__} - {blueprint.name} - {str(e)}"
            )
            continue
    error = f"Failed to update {blueprint.name}, enable debug logs for more information"
    logger.error(error)
    raise Ops2debUpdaterError(error)


async def _find_latest_versions(
    blueprints: list[Blueprint],
) -> tuple[list[LatestRelease], list[Ops2debError]]:
    async with client_factory() as client:
        tasks = [_find_latest_version(client, b) for b in blueprints]
        tasks_results = await asyncio.gather(*tasks, return_exceptions=True)
        releases: list[LatestRelease] = []
        errors: list[Ops2debError] = []
        for result in tasks_results:
            if isinstance(result, Ops2debError):
                errors.append(result)
            elif isinstance(result, LatestRelease):
                releases.append(result)
    return releases, errors


def find_latest_releases(
    resources: Resources,
    fetcher: Fetcher,
    skip_names: list[str] | None,
    only_names: list[str] | None,
) -> Tuple[list[LatestRelease], list[Ops2debError]]:
    blueprints = [b for b in resources.blueprints if b.fetch is not None]
    if skip_names:
        blueprints = [b for b in blueprints if b.name not in skip_names]
    if only_names:
        blueprints = [b for b in blueprints if b.name in only_names]

    # when multiple blueprints have the same name, only look for new releases for the
    # last one in the list
    blueprints_by_name: dict[str, Blueprint] = {}
    for blueprint in blueprints:
        blueprints_by_name[blueprint.name] = blueprint
    blueprints = list(blueprints_by_name.values())

    # find new releases for the selected list of blueprints
    releases, errors = asyncio.run(_find_latest_versions(blueprints))

    # download new files
    releases_by_id: dict[int, LatestRelease] = {}
    for i, release in enumerate(releases):
        releases_by_id[i] = release
        for url in release.blueprint.render_fetch_urls(release.version):
            fetcher.add_task(url, data=i)
    results, failures = fetcher.run_tasks()

    # remove blueprint we can't update because we could not fetch associated files
    for failure in failures:
        releases_by_id.pop(failure.task_data, None)
    releases = list(releases_by_id.values())

    # add new urls to lock file
    for result in results:
        if (a_release := releases_by_id.get(result.task_data)) is not None:
            lock = resources.get_blueprint_lock(a_release.blueprint)
            lock.add([result])

    return releases, list([failure.error for failure in failures]) + list(errors)


def _update_configuration(
    resources: Resources,
    release: LatestRelease,
    max_versions: int,
) -> list[str]:
    raw_blueprint = resources.get_raw_blueprint(release.blueprint)

    removed_versions: list[str] = []
    if max_versions == 1:
        if release.blueprint.matrix and release.blueprint.matrix.versions:
            removed_versions = raw_blueprint["matrix"].pop("versions")
            if not raw_blueprint["matrix"]:
                raw_blueprint.pop("matrix")
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


def _remove_versions_from_lockfile(
    configuration: Resources, release: LatestRelease, removed_versions: list[str]
) -> None:
    blueprint = release.blueprint
    lock = configuration.get_blueprint_lock(blueprint)
    for version in removed_versions:
        lock.remove(blueprint.render_fetch_urls(version))


def _update_configurations(
    resources: Resources, max_versions: int, releases: list[LatestRelease]
) -> list[str]:
    summary: list[str] = []

    for release in releases:
        removed_versions = _update_configuration(resources, release, max_versions)
        if max_versions == 1:
            lines = [
                f"Update {release.blueprint.name} from "
                f"v{release.blueprint.version} to v{release.version}"
            ]
        else:
            lines = [f"Add {release.blueprint.name} v{release.version}"]
            for version in removed_versions:
                lines.append(f"Remove {release.blueprint.name} v{version}")
        _remove_versions_from_lockfile(resources, release, removed_versions)
        summary.extend(lines)

    return summary


def update(
    resources: Resources,
    fetcher: Fetcher,
    dry_run: bool = False,
    output_path: Path | None = None,
    skip_names: list[str] | None = None,
    only_names: list[str] | None = None,
    max_versions: int = 1,
) -> None:
    logger.title("Looking for new releases...")
    releases, errors = find_latest_releases(resources, fetcher, skip_names, only_names)

    summary = _update_configurations(resources, max_versions, releases)

    if not releases:
        logger.info("Did not found any updates")
    else:
        if dry_run is False:
            resources.save()
            logger.info("Lockfile and configuration updated")

    if output_path is not None:
        output_path.write_text("\n".join(summary + [""]))

    if errors:
        raise Ops2debUpdaterError(f"{len(errors)} failures occurred")
