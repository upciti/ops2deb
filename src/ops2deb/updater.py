import asyncio
from typing import Any, List, Optional

import httpx
import typer
from semver.version import Version

from .parser import Blueprint
from .settings import settings


async def _search(
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
        if status >= 500 or status == 404:
            break
        else:
            new_version = version
    return new_version


async def _update(blueprint: Blueprint) -> Optional[Version]:
    async with httpx.AsyncClient() as client:
        old_version = version = Version.parse(blueprint.version)
        version = await _search(client, blueprint, version, False)
        version = await _search(client, blueprint, version, True)
        if version != old_version:
            typer.secho(
                f"* {blueprint.name} can be bumped from {old_version} to {version}",
                fg=typer.colors.WHITE,
            )
            return version
        return None


def update(blueprints: List[Blueprint]) -> bool:
    typer.secho("Looking for new releases...", fg=typer.colors.BLUE, bold=True)

    tasks = []
    for blueprint in blueprints:
        if blueprint.fetch is None:
            continue
        if not Version.isvalid(blueprint.version):
            typer.secho(
                f"* {blueprint.name} is not using semantic versioning",
                fg=typer.colors.YELLOW,
            )
            continue
        tasks.append(_update(blueprint))

    # FIXME: return type
    async def _update_all() -> Any:
        return await asyncio.gather(*tasks)

    results = asyncio.run(_update_all())
    return bool([True for r in results if r is not None])
