import asyncio
import bz2
import gzip
import io
import shutil
import tarfile
from pathlib import Path

import unix_ar
import zstandard

from ops2deb import logger
from ops2deb.exceptions import Ops2debExtractError
from ops2deb.utils import log_and_raise


def _unpack_gz(file_path: str, extract_path: str) -> None:
    output_path = Path(extract_path) / Path(file_path).stem
    with output_path.open("wb") as output:
        with gzip.open(file_path, "rb") as gz_archive:
            shutil.copyfileobj(gz_archive, output)


def _unpack_bz2(file_path: str, extract_path: str) -> None:
    output_path = Path(extract_path) / Path(file_path).stem
    with output_path.open(mode="wb") as output:
        with bz2.open(file_path, "rb") as bz2_archive:
            shutil.copyfileobj(bz2_archive, output)


def _unpack_deb(file_path: str, extract_path: str) -> None:
    ar_file = unix_ar.open(file_path)
    file_names = [info.name.decode("utf-8") for info in ar_file.infolist()]
    for file_name in file_names:
        if file_name.startswith("debian-binary"):
            continue
        tarball = ar_file.open(file_name)
        tar_file = tarfile.open(fileobj=tarball)
        try:
            tar_file.extractall(Path(extract_path) / file_name.split(".")[0])
        finally:
            tar_file.close()


def _unpack_zst(file_path: str, extract_path: str) -> None:
    output_path = Path(extract_path) / Path(file_path).stem
    dctx = zstandard.ZstdDecompressor()
    with open(file_path, "rb") as ifh, output_path.open("wb") as ofh:
        dctx.copy_stream(ifh, ofh)


def _unpack_tar_zst(file_path: str, extract_path: str) -> None:
    dctx = zstandard.ZstdDecompressor()
    with open(file_path, "rb") as ifh, io.BytesIO() as ofh:
        dctx.copy_stream(ifh, ofh)
        ofh.seek(0)
        with tarfile.open(fileobj=ofh) as tar_file:
            tar_file.extractall(extract_path)


shutil.register_unpack_format("gz", [".gz"], _unpack_gz)
shutil.register_unpack_format("bz2", [".bz2"], _unpack_bz2)
shutil.register_unpack_format("deb", [".deb"], _unpack_deb)
shutil.register_unpack_format("zsttar", [".tar.zst"], _unpack_tar_zst)
shutil.register_unpack_format("zst", [".zst"], _unpack_zst)


def is_archive_format_supported(archive_path: Path) -> bool:
    for name, extensions, _ in shutil.get_unpack_formats():
        for extension in extensions:
            if archive_path.name.endswith(extension):
                return True
    return False


async def extract_archive(archive_path: Path, extract_path: Path) -> None:
    tmp_extract_path = f"{extract_path}_tmp"
    Path(tmp_extract_path).mkdir(exist_ok=True)
    logger.info(f"Extracting {archive_path.name}...")

    try:
        await asyncio.get_running_loop().run_in_executor(
            None, shutil.unpack_archive, archive_path, tmp_extract_path
        )
    except Exception as e:
        error = f"Failed to extract archive {archive_path}"
        if str(e):
            error += f" ({e})"
        log_and_raise(Ops2debExtractError(error))

    shutil.move(tmp_extract_path, extract_path)
