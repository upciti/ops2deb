import asyncio
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
from .utils import log_and_raise, separate_successes_from_errors


# fixme: move this somewhere else, this code is also duplicated in formatter.py
class FixIndentEmitter(Emitter):
    def expect_block_sequence(self) -> None:
        self.increase_indent(flow=False, indentless=False)
        self.state = self.expect_first_block_sequence_item


@dataclass(frozen=True)
class LatestRelease:
    blueprint: Blueprint
    version: str


async def _bump_and_poll(
    client: httpx.AsyncClient,
    blueprint: Blueprint,
    version: Version,
    bump_patch: bool = False,
) -> Version:
    new_version = version
    while True:
        version = version.bump_patch() if bump_patch else version.bump_minor()
        if not (remote_file := blueprint.render_fetch(version=str(version))):
            break
        url = remote_file.url
        logger.debug(f"Trying {url}")
        try:
            response = await client.head(url)
        except httpx.HTTPError as e:
            log_and_raise(Ops2debUpdaterError(f"Failed HEAD request to {url}. {str(e)}"))
        status = response.status_code
        if status >= 500:
            log_and_raise(Ops2debUpdaterError(f"Server error when requesting {url}"))
        if status >= 400:
            break
        else:
            new_version = version
    return new_version


async def _find_latest_release(
    blueprint: Blueprint,
) -> Optional[LatestRelease]:
    if blueprint.fetch is None:
        return None

    if not Version.isvalid(blueprint.version):
        logger.warning(f"{blueprint.name} is not using semantic versioning")
        return None

    old_version = version = Version.parse(blueprint.version)
    async with client_factory() as client:
        version = await _bump_and_poll(client, blueprint, version, False)
        version = await _bump_and_poll(client, blueprint, version, True)

    if version != old_version:
        logger.info(f"{blueprint.name} can be bumped from {old_version} to {version}")

        return LatestRelease(
            blueprint=blueprint,
            version=str(version),
        )

    return None


def _find_latest_releases(
    blueprints: List[Blueprint],
) -> List[Optional[Union[LatestRelease, Exception]]]:
    async def run_tasks() -> Any:
        return await asyncio.gather(
            *[_find_latest_release(b) for b in blueprints],
            return_exceptions=True,
        )

    return asyncio.run(run_tasks())


def _fetch_latest_files(
    latest_releases: List[Optional[LatestRelease]],
) -> Dict[str, FetchResultOrError]:
    blueprints = [
        release.blueprint.copy(update={"version": release.version})
        for release in latest_releases
        if release is not None
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
            if isinstance(release, LatestRelease):
                _update_raw_blueprint(raw_blueprint, release, fetch_results)
        with configuration_path.open("w") as output:
            yaml.dump(configuration_dict, output)
        logger.info("Configuration file updated")

        if output_path is not None:
            lines = [
                f"Updated {r.blueprint.name} from {r.blueprint.version} to {r.version}"
                for r in latest_releases
                if isinstance(r, LatestRelease)
            ]
            output_path.write_text("\n".join(lines) + "\n")

    _, fetcher_errors = separate_successes_from_errors(fetch_results.values())
    if fetcher_errors or updater_errors:
        raise Ops2debUpdaterError(
            f"{len(fetcher_errors)+len(updater_errors)} failures occurred"
        )
