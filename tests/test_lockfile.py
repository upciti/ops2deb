from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from ops2deb.exceptions import Ops2debLockFileError
from ops2deb.lockfile import Lock


@dataclass
class UrlAndHash:
    url: str
    sha256: str


def test_init__creates_an_empty_lock_when_lockfile_does_not_exist(lockfile_path):
    # Given
    lock = Lock(lockfile_path)

    # Then
    assert lock._entries == {}


def test_init__raises_when_lockfile_path_is_a_directory(tmp_path):
    # When
    with pytest.raises(Ops2debLockFileError) as error:
        Lock(tmp_path)

    # Then
    assert error.match("Path points to a directory")


def test_init__raises_when_lockfile_path_contains_invalid_yaml(lockfile_path):
    # Given
    lockfile_path.write_text("@£¢±")

    # When
    with pytest.raises(Ops2debLockFileError) as error:
        Lock(lockfile_path)

    # Then
    assert error.match("Invalid YAML file")


def test_init__raises_when_lockfile_cannot_be_parsed_with_pydantic(lockfile_path):
    # Given
    lockfile_path.write_text("1")

    # When
    with pytest.raises(Ops2debLockFileError) as error:
        Lock(lockfile_path)

    # Then
    assert error.match("Invalid lockfile")


def test_sha256__raises_when_url_is_not_in_cache(lockfile_path):
    # Given
    url = "http://tests.com/file.tar.gz"

    # When
    with pytest.raises(Ops2debLockFileError) as error:
        Lock(lockfile_path).sha256(url)

    # Then
    assert error.match(f"Unknown hash for url {url}, please run ops2deb lock")


def test_save__should_not_create_a_file_when_lock_is_empty(lockfile_path):
    # Given
    lock = Lock(lockfile_path)

    # When
    lock.save()

    # Then
    assert lockfile_path.exists() is False


def test_save__produces_a_lockfile_that_contains_added_entries(lockfile_path):
    # Given
    lock = Lock(lockfile_path)
    lock.add([UrlAndHash("http://tests.com/file.tar.gz", "deadbeef")])

    # When
    lock.save()

    # Then
    assert Lock(lockfile_path).sha256("http://tests.com/file.tar.gz") == "deadbeef"


@patch("yaml.dump")
def test_save__should_not_write_file_when_no_entry_have_been_added_nor_removed(
    mock_dump, lockfile_path
):
    # Given
    lock = Lock(lockfile_path)

    # When
    lock.save()

    # Then
    mock_dump.assert_not_called()


def test_save__sets_the_same_timestamp_to_added_entries(lockfile_path):
    # Given
    lock = Lock(lockfile_path)
    lock.add([UrlAndHash("http://tests.com/file1.tar.gz", "deadbeef")])
    lock.add([UrlAndHash("http://tests.com/file2.tar.gz", "deadbeef")])

    # When
    lock.save()

    # Then
    lock = Lock(lockfile_path)
    timestamp_1 = lock.timestamp("http://tests.com/file1.tar.gz")
    timestamp_2 = lock.timestamp("http://tests.com/file2.tar.gz")
    assert timestamp_1 == timestamp_2


@patch("ops2deb.lockfile.datetime")
def test_save__should_not_include_microseconds_in_timestamps(
    mock_datetime, lockfile_path
):
    # Given
    mock_datetime.now.return_value = datetime(
        2023, 3, 4, 0, 22, 14, 1234, tzinfo=timezone.utc
    )
    lock = Lock(lockfile_path)
    lock.add([UrlAndHash("http://tests.com/file1.tar.gz", "deadbeef")])

    # When
    lock.save()

    # Then
    assert "2023-03-04 00:22:14+00:00" in lockfile_path.read_text()


@patch("ops2deb.lockfile.datetime")
def test_save__should_be_idempotent(mock_datetime, lockfile_path):
    # Given
    mock_datetime.now.return_value = datetime(2023, 3, 4, 0, 22, 14, tzinfo=timezone.utc)

    # When
    lock = Lock(lockfile_path)
    lock.add([UrlAndHash("http://tests.com/file1.tar.gz", "deadbeef")])
    lock.add([UrlAndHash("http://tests.com/file2.tar.gz", "deadbeef")])
    lock.save()
    lockfile_content_0 = lockfile_path.read_text()
    Lock(lockfile_path).save()
    lockfile_content_1 = lockfile_path.read_text()
    print(lockfile_content_0)

    # Then
    assert lockfile_content_0 == lockfile_content_1


@patch("ops2deb.lockfile.datetime")
def test_save__should_not_use_yaml_anchors_in_timestamps(mock_datetime, lockfile_path):
    # happens when you reference an object multiple time in a YAML document and when the
    # pyyaml dumper is not configured to "ignore aliases"

    # Given
    mock_datetime.now.return_value = datetime(2023, 3, 4, 0, 22, 14, tzinfo=timezone.utc)

    # When
    lock = Lock(lockfile_path)
    lock.add([UrlAndHash("http://tests.com/file1.tar.gz", "deadbeef")])
    lock.add([UrlAndHash("http://tests.com/file2.tar.gz", "deadbeef")])
    lock.save()

    # Then
    assert "timestamp: &" not in lockfile_path.read_text()
