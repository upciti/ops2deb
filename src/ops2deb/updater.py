import asyncio
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import ruamel.yaml
import typer
from pydantic import BaseModel
from semver.version import Version

from .fetcher import download
from .parser import Blueprint, load, validate
from .settings import settings


class NewRelease(BaseModel):
    file_path: Path
    sha256: str
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
        if (remote_file := blueprint.render(version=str(version)).fetch) is None:
            break
        url = remote_file.url
        if settings.verbose:
            typer.secho(f"Trying {url}", fg=typer.colors.BRIGHT_BLACK)
        response = await client.head(url)
        status = response.status_code
        # FIXME: retry once on 500
        if status >= 400:
            break
        else:
            new_version = version
    return new_version


async def _find_latest_release(
    blueprint: Blueprint,
) -> Optional[NewRelease]:

    if not Version.isvalid(blueprint.version):
        typer.secho(
            f"* {blueprint.name} is not using semantic versioning",
            fg=typer.colors.YELLOW,
        )
        return None

    old_version = version = Version.parse(blueprint.version)
    async with httpx.AsyncClient() as client:
        version = await _bump_and_poll(client, blueprint, version, False)
        version = await _bump_and_poll(client, blueprint, version, True)

    if version != old_version:
        typer.secho(
            f"* {blueprint.name} can be bumped from {old_version} to {version}",
            fg=typer.colors.WHITE,
        )

        file_path, sha256 = await download(
            blueprint.render(version=str(version)).fetch.url  # type: ignore
        )
        return NewRelease(
            file_path=file_path,
            sha256=sha256,
            version=str(version),
        )

    return None


async def _update_blueprint_dict(blueprint_dict: Dict[str, Any]) -> bool:
    blueprint = Blueprint.parse_obj(blueprint_dict)
    if blueprint.fetch is None:
        return True

    release = await _find_latest_release(blueprint)
    if release is None:
        return False

    blueprint_dict["version"] = release.version
    blueprint_dict["fetch"]["sha256"] = release.sha256
    return True


def update(configuration_path: Path, dry_run: bool = False) -> bool:
    yaml = ruamel.yaml.YAML()
    configuration_dict = load(configuration_path, yaml)
    validate(configuration_dict)

    typer.secho("Looking for new releases...", fg=typer.colors.BLUE, bold=True)

    async def run_tasks() -> Any:
        return await asyncio.gather(
            *[_update_blueprint_dict(b) for b in configuration_dict]
        )

    results = asyncio.run(run_tasks())

    if dry_run is False:
        with configuration_path.open("w") as output:
            yaml.dump(configuration_dict, output)
        typer.secho("Configuration file updated", fg=typer.colors.BLUE, bold=True)

    return bool(list(results))
