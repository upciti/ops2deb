import base64
from pathlib import Path

import pytest

from ops2deb.exceptions import Ops2debExtractError
from ops2deb.fetcher import extract_archive, is_archive_format_supported


@pytest.fixture
def decode_and_write(tmp_path):
    def _decode_and_write(extension: str, content: bytes) -> Path:
        file_path = tmp_path / f"archive.{extension}"
        file_path.write_bytes(base64.b64decode(content))
        return file_path

    return _decode_and_write


@pytest.fixture
def debian_package(decode_and_write) -> Path:
    return decode_and_write(
        "deb",
        b"""ITxhcmNoPgpkZWJpYW4tYmluYXJ5ICAgMTU1NzI2MTA5MCAgMCAgICAgMCAgICAgMTAwNjQ0ICA0
        ICAgICAgICAgYAoyLjAKY29udHJvbC50YXIueHogIDE1NTcyNjEwOTAgIDAgICAgIDAgICAgIDEw
        MDY0NCAgNTA4ICAgICAgIGAK/Td6WFoAAATm1rRGBMC6A4BQIQEWAAAAAAAAANJn5+7gJ/8Bsl0A
        Fwu8HH0BlcAdSj55FcLMJqNZOsvf9I59X9KoocqHYQ0azB90t59hNAb5EjVDgV/WKpgKFddy29jY
        qi6i7kd2fQzBU0hIaxglDu/0ywroODUYazi6TIEM87WBbYFLnj6qd8Wqd0BJZnaUrBDW30f9ay2Y
        iSK5Tm+RSxkwUnUidL8fOwc4G3iSb+YcFE4tjMqZzsB3iXy09aUKZHAOgqMfsWI413VrN+qWmq/k
        EcT8b+7h6V+8hx9tPvO8vm9YpBtOsQqljCimTUom+RRNYlGyyvbdMO2mFznoLZNFFzr2UDIefi0i
        bbCATw2UbjPn2lb+BOQGIIkepvthCG7H2/eej6RT0xzYd3TAVpo/owQJ5/WYMQP3ntkQXqknKaI+
        BTFFexH2buKt+yYRgV1BKjjbzq3EDyllvS6K7O5cJ6vfGYTa3FI5dNz1byZejuyAVbTrHRpOct9Y
        xd5C9Pfk4Rw57eWKoT/uSFwY6nYTdDl+3V4XLB/kecfxsWaWiPwsb+bWOAD7rOLJ9mrnAfr4G8Ej
        S/V5Tmz3Qfb5CerM192Wsp3Hr0N8sWCbSeCn8q3/P6/LkwAAAADN29w8vEq9MwAB1gOAUAAAtMl3
        wbHEZ/sCAAAAAARZWmRhdGEudGFyLnh6ICAgICAxNTU3MjYxMDkwICAwICAgICAwICAgICAxMDA2
        NDQgIDUyOCAgICAgICBgCv03elhaAAAE5ta0RgTAzgOAUCEBFgAAAAAAAACMWyQd4Cf/AcZdABcL
        vBx9AZXAHUo+eRXCzCajWTrL3/SOfV/SqKHKh2ENGswfdLefYTQG+RI1Q4Ff1iqYChXXctvY2LBN
        WwNtDSJ5udM5LXLbDOxBbrl8iBf02q1tQRTzPRI59OWOlnoYU0LJ6LfF0kaSs9aMIC45NCmdvED/
        2CbMIbkDE+RnhcyDeqy9jz0zPINeA/Y0tWkP6jpvcTIbs6ry7GNYUu6HItBtJI+CbAs+gt3qUMYp
        x85z49jiIKGQwCcmfeCXE8HjMJ17+pjHOhD2P3xt2LkzDdQlod5XzB4/sZNudqCVTBSg9aJ2r0gH
        4BLjo3kHTIqzzpKSLgsd4y3A3ZTr26KMxoV51DYf5x7fb0Csp3kcm44/w7pJps/RrXcXLcOYNmlO
        IiIGVe7dK06oCnsMaXR/ru8Qd8YbExZNoPv+q23oHC4hx3c797cHYN1+iiMuRHSjI8dbg3eTAhEd
        SsiDHDta6pXQ2VHkXGAXP1gpYrvldasQq7xNR3epiYqBKW612hpI3ghzcNRR5jFSem/WJZFhzYOC
        QUIyE5HGDNLzZ62wiIpGNsNkJxJQoS9cEHKz+pnwgeeyox/AqY7jvg+nVn3pX7eGv/Ot6gAAAABj
        WR47+Dd1CgAB6gOAUAAAYg7PsrHEZ/sCAAAAAARZWg==
        """,
    )


@pytest.fixture
def truncate_file():
    def _truncate_file(file_path: Path, size: int) -> Path:
        content = file_path.read_bytes()
        file_path.write_bytes(content[:size])
        return file_path

    return _truncate_file


@pytest.fixture
def extract_path(tmp_path) -> Path:
    return tmp_path / "extracted"


def test_is_archive_format_supported_should_return_false_when_file_is_not_an_archive(
    tmp_path,
):
    file_path = tmp_path / "file"
    file_path.touch()
    assert is_archive_format_supported(file_path) is False


@pytest.mark.parametrize(
    "extension",
    ["deb", "zip", "bz2", "tar", "tar.bz2", "tar.xz", "tar.gz", "gz", "zst", "tar.zst"],
)
def test_is_archive_format_supported_should_return_true_when_archive_format_is_supported(
    tmp_path,
    extension,
):
    file_path = tmp_path / f"file.{extension}"
    file_path.touch()
    assert is_archive_format_supported(file_path) is True


async def test_extract_archive_should_extract_data_and_control_tar_when_archive_is_a_deb(
    debian_package, extract_path
):
    await extract_archive(debian_package, extract_path)
    assert (extract_path / "control").is_dir()
    assert (extract_path / "data").is_dir()
    assert (extract_path / "data/usr/bin/great-app").is_file()


async def test_extract_archive_should_raise_read_error_when_archive_is_an_invalid_deb(
    debian_package, extract_path, truncate_file
):
    with pytest.raises(Ops2debExtractError):
        await extract_archive(truncate_file(debian_package, 1000), extract_path)


async def test_extract_archive_should_extract_bzip2_archive_when_file_name_ends_with_bz2(
    extract_path, decode_and_write
):
    archive_path = decode_and_write(
        "bz2", b"QlpoOTFBWSZTWRpUZJIAAAAFAABAAgSgACGaaDNNEzOLuSKcKEgNKjJJAA=="
    )
    await extract_archive(archive_path, extract_path)
    assert (extract_path / "archive").is_file()
    assert (extract_path / "archive").read_text() == "Hello"


async def test_extract_archive_should_extract_gz_archive_when_file_name_ends_with_gz(
    extract_path, decode_and_write
):
    archive_path = decode_and_write("gz", b"H4sICCqjO2IAA2hlbGxvAPNIzcnJBwCCidH3BQAAAA==")
    await extract_archive(archive_path, extract_path)
    assert (extract_path / "archive").is_file()
    assert (extract_path / "archive").read_text() == "Hello"


async def test_extract_archive_should_extract_zst_archive_when_file_name_ends_with_zst(
    extract_path, decode_and_write
):
    archive_path = decode_and_write("zst", b"KLUv/SQLWQAASGVsbG8gc2lyIQqkAdJw")
    await extract_archive(archive_path, extract_path)
    assert (extract_path / "archive").is_file()
    assert (extract_path / "archive").read_text() == "Hello sir!\n"


async def test_extract_archive_should_extract_tar_zst_archive_when_file_name_ends_with_tar_zst(  # noqa: E501
    extract_path, decode_and_write
):
    archive_path = decode_and_write(
        "tar.zst",
        b"KLUv/QRYvQIAckQPFqCpDQCWnGiqCF+dELPSlpos3N4uRAoGgrSLCispQZTNFmMpxjef1Lv7qPgh8+B"
        b"ZT+j9N2b+v1ezhLSLjggA9QXwTANUDIUDwFYAkAa41gpXbwJdP2F1GA==",
    )
    await extract_archive(archive_path, extract_path)
    assert (extract_path / "hello.txt").read_text() == "Hello!\n"
