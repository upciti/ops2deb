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
from .fetcher import Fetcher, FetchTask
from .parser import Blueprint, RemoteFile, load, validate
from .utils import log_and_raise, split_successes_from_errors


# fixme: move this somewhere else, this code is also duplicated in formatter.py
class FixIndentEmitter(Emitter):
    def expect_block_sequence(self) -> None:
        self.increase_indent(flow=False, indentless=False)
        self.state = self.expect_first_block_sequence_item


@dataclass(frozen=True)
class NewRelease:
    name: str
    sha256: str
    old_version: str
    new_version: str


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

        file = cast(RemoteFile, blueprint.render(version=str(version)).fetch)
        result = await FetchTask(Fetcher.cache_directory_path, file).fetch(extract=False)
        return NewRelease(
            name=blueprint.name,
            sha256=result.sha256_sum,
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
    blueprint_dict.pop("revision", None)
    return release


def update(
    configuration_path: Path, dry_run: bool = False, output_path: Optional[Path] = None
) -> None:
    yaml = ruamel.yaml.YAML(typ="rt")
    yaml.Emitter = FixIndentEmitter

    configuration_dict = load(configuration_path, yaml)
    validate(configuration_dict)

    logger.title("Looking for new releases...")

    # configuration file can be a list of blueprints or a single blueprint
    blueprints_dict = (
        configuration_dict
        if isinstance(configuration_dict, list)
        else [configuration_dict]
    )

    async def run_tasks() -> Any:
        return await asyncio.gather(
            *[_update_blueprint_dict(b) for b in blueprints_dict],
            return_exceptions=True,
        )

    results: List[Union[NewRelease, Exception]] = asyncio.run(run_tasks())
    new_releases, errors = split_successes_from_errors(results)

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
        raise Ops2debUpdaterError(f"{len(errors)} failures occurred")
