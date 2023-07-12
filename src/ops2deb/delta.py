from pydantic import BaseModel

from ops2deb.apt import DebianRepositoryPackage
from ops2deb.parser import Blueprint


class StateDelta(BaseModel):
    added: list[DebianRepositoryPackage]
    removed: list[DebianRepositoryPackage]


def compute_state_delta(
    packages: list[DebianRepositoryPackage], blueprints: list[Blueprint]
) -> StateDelta:
    blueprint_slugs = set()
    for blueprint in blueprints:
        epoch = f"{blueprint.epoch}:" if blueprint.epoch else ""
        for version in blueprint.versions():
            for architecture in blueprint.architectures():
                debian_version = f"{epoch}{version}-{blueprint.revision}~ops2deb"
                blueprint_slugs.add(f"{blueprint.name}_{debian_version}_{architecture}")

    package_slugs = set()
    for package in packages:
        package_slugs.add(f"{package.name}_{package.version}_{package.architecture}")

    common_slugs = blueprint_slugs.intersection(package_slugs)
    new_slugs = blueprint_slugs - common_slugs
    deleted_slugs = package_slugs - common_slugs

    state_delta = StateDelta.model_construct(added=[], removed=[])
    for slug in new_slugs:
        name, version, architecture = slug.split("_")
        state_delta.added.append(DebianRepositoryPackage(name, version, architecture))
    for slug in deleted_slugs:
        name, version, architecture = slug.split("_")
        state_delta.removed.append(DebianRepositoryPackage(name, version, architecture))

    state_delta.added.sort()
    state_delta.removed.sort()

    return state_delta
