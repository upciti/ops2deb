import asyncio
import hashlib
import shutil
from pathlib import Path

import aiofiles
import httpx
import typer

from .parser import RemoteFile

_cache_path: Path = Path("/tmp/ops2deb_cache")


def purge_cache() -> None:
    shutil.rmtree(_cache_path, ignore_errors=True)


async def run(program: str, *args: str, cwd: Path) -> asyncio.subprocess.Process:
    proc = await asyncio.create_subprocess_exec(
        program,
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    return proc


async def untar(file_path: Path) -> None:
    proc = await run("/bin/tar", "zxf", str(file_path), cwd=file_path.parent)
    if proc.returncode:
        raise RuntimeError(f"Failed to untar archive {file_path.name}")
    else:
        file_path.unlink()


async def sha256(file_path: Path) -> str:
    sha256_hash = hashlib.sha256()
    with file_path.open("rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
            await asyncio.sleep(0)
    return sha256_hash.hexdigest()


async def download(url: str, file_path: Path) -> None:
    tmp_path = f"{file_path}.part"
    async with httpx.AsyncClient() as client:
        async with client.stream("GET", url) as r:
            # FIXME: https://github.com/Tinche/aiofiles/issues/91
            async with aiofiles.open(tmp_path, "wb") as f:  # type: ignore
                r.raise_for_status()
                async for chunk in r.aiter_bytes():
                    await f.write(chunk)
    shutil.move(tmp_path, file_path)


def log(msg: str) -> None:
    typer.secho(f"* {msg}", fg=typer.colors.WHITE)


async def fetch(remote_file: RemoteFile, output_path: Path) -> None:
    _cache_path.mkdir(exist_ok=True)
    url_hash = hashlib.sha256(remote_file.url.encode()).hexdigest()
    file_name = remote_file.url.split("/")[-1]
    file_path = _cache_path / f"{url_hash}_{file_name}"

    if not file_path.is_file():
        log(f"Downloading {file_name}...")
        await download(remote_file.url, file_path)

    log(f"Verifying {file_name}...")
    digest = await sha256(file_path)
    if digest != remote_file.sha256:
        raise ValueError(
            f"Wrong checksum for file {file_name}. "
            f"Expected {remote_file.sha256}, got {digest}."
        )
    shutil.copy(file_path, output_path / file_name)

    if file_name.endswith(".tar.gz"):
        log(f"Extracting {file_name}...")
        await untar(output_path / file_name)

    log(f"Done with {file_name}")
