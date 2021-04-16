import asyncio
from pathlib import Path
from typing import Any, Optional, Tuple

import httpx
import typer
from pydantic import BaseModel
from semver.version import Version

from .fetcher import download
from .parser import Blueprint, parse
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
) -> Tuple[Blueprint, Optional[NewRelease]]:

    if blueprint.fetch is None:
        return blueprint, None

    if not Version.isvalid(blueprint.version):
        typer.secho(
            f"* {blueprint.name} is not using semantic versioning",
            fg=typer.colors.YELLOW,
        )
        return blueprint, None

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
        return blueprint, NewRelease(
            file_path=file_path,
            sha256=sha256,
            version=str(version),
        )

    return blueprint, None


def update(config: Path, dry_run: bool = False) -> bool:
    blueprints = parse(config).__root__

    typer.secho("Looking for new releases...", fg=typer.colors.BLUE, bold=True)

    async def run_tasks() -> Any:
        return await asyncio.gather(*[_find_latest_release(b) for b in blueprints])

    results = asyncio.run(run_tasks())

    raw_config = config.read_text()
    for blueprint, release in results:
        if release is not None:
            # FIXME: what if two blueprint have the same version?
            raw_config = raw_config.replace(blueprint.version, release.version)
            raw_config = raw_config.replace(blueprint.fetch.sha256, release.sha256)

    if dry_run is False:
        config.write_text(raw_config)
        typer.secho("Configuration file updated", fg=typer.colors.BLUE, bold=True)

    return bool([True for b, r in results if r is not None])
