import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import ruamel.yaml
from semver.version import Version

from . import logger
from .client import client_factory
from .exceptions import Ops2debError, UpdaterError
from .fetcher import download_file_to_cache
from .parser import Blueprint, load, validate


@dataclass(frozen=True)
class NewRelease:
    name: str
    file_path: Path
    sha256: str
    old_version: str
    new_version: str


def _error(msg: str) -> None:
    logger.error(msg)
    raise UpdaterError(msg)


async def _bump_and_poll(
    client: httpx.AsyncClient,
    blueprint: Blueprint,
    version: Version,
    bump_patch: bool = False,
) -> Version:
    new_version = version
    while True:
        version = version.bump_patch() if bump_patch else version.bump_minor()
        if (remote_file := blueprint.render(version=str(version)).fetch) is None:
            break
        url = remote_file.url
        logger.debug(f"Trying {url}")
        try:
            response = await client.head(url)
        except httpx.HTTPError as e:
            _error(f"Failed HEAD request to {url}. {str(e)}")
        status = response.status_code
        if status >= 500:
            _error(f"Server error when requesting {url}")
        if status >= 400:
            break
        else:
            new_version = version
    return new_version


async def _find_latest_release(
    blueprint: Blueprint,
) -> Optional[NewRelease]:

    if not Version.isvalid(blueprint.version):
        logger.warning(f"{blueprint.name} is not using semantic versioning")
        return None

    old_version = version = Version.parse(blueprint.version)
    async with client_factory() as client:
        version = await _bump_and_poll(client, blueprint, version, False)
        version = await _bump_and_poll(client, blueprint, version, True)

    if version != old_version:
        logger.info(f"{blueprint.name} can be bumped from {old_version} to {version}")

        file_path, sha256 = await download_file_to_cache(
            blueprint.render(version=str(version)).fetch.url  # type: ignore
        )
        return NewRelease(
            name=blueprint.name,
            file_path=file_path,
            sha256=sha256,
            old_version=blueprint.version,
            new_version=str(version),
        )

    return None


async def _update_blueprint_dict(blueprint_dict: Dict[str, Any]) -> Optional[NewRelease]:
    blueprint = Blueprint.parse_obj(blueprint_dict)
    if blueprint.fetch is None:
        return None

    release = await _find_latest_release(blueprint)
    if release is None:
        return None

    blueprint_dict["version"] = release.new_version
    blueprint_dict["fetch"]["sha256"] = release.sha256
    if "revision" in blueprint_dict.keys():
        blueprint_dict["revision"] = 1
    return release


def update(
    configuration_path: Path, dry_run: bool = False, output_path: Optional[Path] = None
) -> None:
    yaml = ruamel.yaml.YAML()
    configuration_dict = load(configuration_path, yaml)
    validate(configuration_dict)

    logger.title("Looking for new releases...")

    async def run_tasks() -> Any:
        return await asyncio.gather(
            *[_update_blueprint_dict(b) for b in configuration_dict],
            return_exceptions=True,
        )

    results = asyncio.run(run_tasks())
    new_releases = [r for r in results if isinstance(r, NewRelease)]
    errors = [e for e in results if isinstance(e, Exception)]

    for error in errors:
        if not isinstance(error, Ops2debError):
            raise error

    if dry_run is False and new_releases:
        with configuration_path.open("w") as output:
            yaml.dump(configuration_dict, output)
        logger.info("Configuration file updated")

        if output_path is not None:
            lines = [
                f"Updated {r.name} from {r.old_version} to {r.new_version}"
                for r in new_releases
            ]
            output_path.write_text("\n".join(lines) + "\n")

    if not new_releases:
        logger.info("Did not found any updates")

    if errors:
        raise UpdaterError(f"{len(errors)} failures occurred")
