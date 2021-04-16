import asyncio
import hashlib
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

import aiofiles
import httpx
import typer

_cache_path: Path = Path("/tmp/ops2deb_cache")


def log(msg: str) -> None:
    typer.secho(f"* {msg}", fg=typer.colors.WHITE)


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


async def extract(file_path: Path) -> None:
    commands = [
        ({".tar.gz", ".tar.xz", ".tar"}, ["/bin/tar", "xf", str(file_path)]),
        ({".zip"}, ["/bin/unzip", str(file_path)]),
    ]
    selected_command: Optional[List[str]] = None
    for extensions, command in commands:
        for extension in extensions:
            if file_path.name.endswith(extension):
                selected_command = command
                break
    if selected_command is not None:
        log(f"Extracting {file_path.name}...")
        proc = await run(*selected_command, cwd=file_path.parent)
        if proc.returncode:
            raise RuntimeError(f"Failed to extract archive {file_path.name}")
        else:
            file_path.unlink()


async def compute_checksum(file_path: Path) -> str:
    sha256_hash = hashlib.sha256()
    with file_path.open("rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
            await asyncio.sleep(0)
    return sha256_hash.hexdigest()


async def download(url: str, expected_hash: Optional[str] = None) -> Tuple[Path, str]:
    _cache_path.mkdir(exist_ok=True)
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    file_name = url.split("/")[-1]
    file_path = _cache_path / f"{url_hash}_{file_name}"
    tmp_path = f"{file_path}.part"

    if not file_path.is_file():
        log(f"Downloading {file_name}...")
        async with httpx.AsyncClient() as client:
            async with client.stream("GET", url) as r:
                # FIXME: https://github.com/Tinche/aiofiles/issues/91
                async with aiofiles.open(tmp_path, "wb") as f:  # type: ignore
                    r.raise_for_status()
                    async for chunk in r.aiter_bytes():
                        await f.write(chunk)
        shutil.move(tmp_path, file_path)

    log(f"Computing checksum for {file_name}...")
    computed_hash = await compute_checksum(file_path)

    if expected_hash is not None:
        if computed_hash != expected_hash:
            raise ValueError(
                f"Wrong checksum for file {file_name}. "
                f"Expected {expected_hash}, got {computed_hash}."
            )

    return file_path, computed_hash


async def fetch(url: str, expected_hash: str, save_path: Path) -> None:
    file_name = url.split("/")[-1]
    file_path, _ = await download(url, expected_hash)
    shutil.copy(file_path, save_path / file_name)
    await extract(save_path / file_name)
    log(f"Done with {file_name}")
