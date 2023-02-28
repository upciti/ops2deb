from unittest.mock import patch

import pytest

from ops2deb.exceptions import Ops2debLockFileError
from ops2deb.fetcher import FetchResult
from ops2deb.lockfile import Lock


def test__init__should_create_empty_lock_when_lockfile_does_not_exist(lockfile_path):
    lock = Lock(lockfile_path)
    assert lock._entries == {}


def test__init__should_raise_when_lockfile_path_is_a_directory(tmp_path):
    with pytest.raises(Ops2debLockFileError) as error:
        Lock(tmp_path)
    assert error.match("Path points to a directory")


def test__init__should_raise_when_lockfile_path_contains_invalid_yaml(lockfile_path):
    lockfile_path.write_text("@£¢±")
    with pytest.raises(Ops2debLockFileError) as error:
        Lock(lockfile_path)
    assert error.match("Invalid YAML file")


def test__init__should_raise_when_lockfile_cannot_be_parsed_with_pydantic(lockfile_path):
    lockfile_path.write_text("1")
    with pytest.raises(Ops2debLockFileError) as error:
        Lock(lockfile_path)
    assert error.match("Invalid lockfile")


def test_sha256__should_raise_when_url_is_not_in_cache(lockfile_path):
    url = "http://tests.com/file.tar.gz"
    with pytest.raises(Ops2debLockFileError) as error:
        Lock(lockfile_path).sha256(url)
    assert error.match(f"Unknown hash for url {url}, please run ops2deb lock")


def test_save__should_not_create_a_file_when_lock_is_empty(lockfile_path):
    Lock(lockfile_path).save()
    assert lockfile_path.exists() is False


def test_save__should_produce_a_lockfile_that_contains_added_entries(
    lockfile_path, tmp_path
):
    lock = Lock(lockfile_path)
    lock.add([FetchResult("http://tests.com/file.tar.gz", "deadbeef", tmp_path, None)])
    lock.save()
    lock = Lock(lockfile_path)
    assert lock.sha256("http://tests.com/file.tar.gz") == "deadbeef"


@patch("yaml.dump")
def test_save__should_not_write_file_when_no_entry_have_been_added_nor_removed(
    mock_dump, lockfile_path
):
    lock = Lock(lockfile_path)
    lock.save()
    mock_dump.assert_not_called()


def test_save__set_the_same_timestamp_to_added_entries(lockfile_path, tmp_path):
    lock = Lock(lockfile_path)
    lock.add([FetchResult("http://tests.com/file1.tar.gz", "deadbeef", tmp_path, None)])
    lock.add([FetchResult("http://tests.com/file2.tar.gz", "deadbeef", tmp_path, None)])
    lock.save()
    lock = Lock(lockfile_path)
    timestamp_1 = lock.timestamp("http://tests.com/file1.tar.gz")
    timestamp_2 = lock.timestamp("http://tests.com/file2.tar.gz")
    assert timestamp_1 == timestamp_2
