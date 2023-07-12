from datetime import datetime, timezone
from operator import itemgetter
from pathlib import Path
from typing import Protocol, Sequence

import yaml
from pydantic import BaseModel, RootModel, ValidationError

from ops2deb.exceptions import Ops2debLockFileError
from ops2deb.utils import PrettyYAMLDumper


class UrlAndHash(Protocol):
    url: str
    sha256: str


class LockEntry(BaseModel):
    url: str
    sha256: str
    timestamp: datetime


LockFileModel = RootModel[list[LockEntry]]


def get_utc_datetime() -> datetime:
    return datetime.now(tz=timezone.utc).replace(microsecond=0)


class LockFile:
    def __init__(self, lockfile_path: Path) -> None:
        self.lock_file_path = lockfile_path
        self._entries: dict[str, LockEntry] = {}
        self._tainted: bool = False
        self._new_urls: set[str] = set()
        try:
            if lockfile_path.exists() is True:
                with lockfile_path.open("r") as reader:
                    raw_lockfile = yaml.load(reader, yaml.SafeLoader)
                lockfile = LockFileModel.model_validate(raw_lockfile).root
                self._entries.update({entry.url: entry for entry in lockfile})
        except yaml.YAMLError as e:
            raise Ops2debLockFileError(f"Invalid YAML file.\n{e}")
        except IsADirectoryError:
            raise Ops2debLockFileError(
                f"Path points to a directory: {lockfile_path.absolute()}"
            )
        except ValidationError as e:
            raise Ops2debLockFileError(f"Invalid lockfile.\n{e}")

    def __contains__(self, url: str) -> bool:
        return url in self._entries

    def sha256(self, url: str) -> str:
        try:
            return self._entries[url].sha256
        except KeyError:
            raise Ops2debLockFileError(
                f"Unknown hash for url {url}, please run ops2deb lock"
            )

    def timestamp(self, url: str) -> datetime:
        return self._entries[url].timestamp

    def add(self, entries: Sequence[UrlAndHash]) -> None:
        for entry in entries:
            if (url := str(entry.url)) not in self._entries:
                self._entries[url] = LockEntry(
                    url=str(entry.url),
                    sha256=entry.sha256,
                    timestamp=get_utc_datetime(),
                )
                self._new_urls.add(url)
                self._tainted = True

    def remove(self, urls: Sequence[str]) -> None:
        for url in urls:
            if self._entries.pop(url, None) is not None:
                self._tainted = True

    def save(self) -> None:
        if not self._entries or self._tainted is False:
            return

        # make sure all added urls since lock was created have the same timestamp
        # and make sure this timestamp is when save() was called
        now = get_utc_datetime()
        for new_url in self._new_urls:
            if (entry := self._entries.get(new_url, None)) is not None:
                entry.timestamp = now

        # sort lockfile entries by urls
        entries = [entry.model_dump() for entry in self._entries.values()]
        sorted_entries = sorted(entries, key=itemgetter("timestamp", "url"))

        with self.lock_file_path.open("w") as output:
            yaml.dump(
                sorted_entries,
                output,
                Dumper=PrettyYAMLDumper,
                default_flow_style=False,
                sort_keys=False,
                encoding="utf-8",
            )
        self._tainted = False
