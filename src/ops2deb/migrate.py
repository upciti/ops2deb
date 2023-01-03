from pathlib import Path
from typing import Any, cast

from ops2deb.formatter import format
from ops2deb.lockfile import Lock, UrlAndHash
from ops2deb.parser import RemoteFile, extend, parse


def migrate_blueprint(blueprint: dict[str, Any]) -> None:
    if blueprint_fetch := blueprint.get("fetch", None):
        if isinstance(blueprint_fetch, dict):
            if "targets" not in blueprint_fetch:
                blueprint["fetch"] = blueprint_fetch["url"]
            if blueprint_sha256 := blueprint_fetch.pop("sha256", None):
                if isinstance(blueprint_sha256, dict):
                    blueprint["matrix"] = {"architectures": list(blueprint_sha256.keys())}


def migrate_configuration_file(configuration_path: Path, lockfile_path: Path) -> None:
    lock = Lock(lockfile_path)
    blueprints = extend(parse(configuration_path))

    for blueprint in blueprints:
        if isinstance(fetch := blueprint.render_fetch(), RemoteFile):
            lock.add([cast(UrlAndHash, fetch)])
    lock.save()

    format(configuration_path, migrate_blueprint)
