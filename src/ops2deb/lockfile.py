from datetime import datetime, timezone
from operator import itemgetter
from pathlib import Path
from typing import Protocol, Sequence

import yaml
from pydantic import BaseModel, ValidationError

from ops2deb.exceptions import Ops2debLockFileError
from ops2deb.utils import PrettyYAMLDumper


class UrlAndHash(Protocol):
    url: str
    sha256: str


class LockEntry(BaseModel):
    url: str
    sha256: str
    timestamp: datetime


class LockFile(BaseModel):
    __root__: list[LockEntry]


class Lock:
    def __init__(self, lockfile_path: Path) -> None:
        self.lock_file_path = lockfile_path
        self._entries: dict[str, LockEntry] = {}
        try:
            if lockfile_path.exists() is True:
                with lockfile_path.open("r") as reader:
                    raw_lockfile = yaml.load(reader, yaml.SafeLoader)
                lockfile = LockFile.parse_obj(raw_lockfile).__root__
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

    def add(self, entries: Sequence[UrlAndHash]) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()[:-13] + "Z"
        for entry in entries:
            if (url := str(entry.url)) not in self._entries:
                self._entries[url] = LockEntry(
                    url=str(entry.url), sha256=entry.sha256, timestamp=now
                )

    def remove(self, urls: Sequence[str]) -> None:
        for url in urls:
            self._entries.pop(url, None)

    def save(self) -> None:
        if not self._entries:
            return
        entries = [entry.dict() for entry in self._entries.values()]
        with self.lock_file_path.open("w") as output:
            yaml.dump(
                sorted(entries, key=itemgetter("url")),
                output,
                Dumper=PrettyYAMLDumper,
                default_flow_style=False,
                sort_keys=False,
                encoding="utf-8",
            )
