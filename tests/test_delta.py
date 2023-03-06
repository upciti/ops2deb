from ops2deb.apt import DebianRepositoryPackage
from ops2deb.delta import (
    compute_state_delta,
)


def test_compute_state_delta__contains_new_packages(blueprint_factory):
    # Given
    packages = [DebianRepositoryPackage("kubectl", "1.0.0-1~ops2deb", "amd64")]
    blueprints = [
        blueprint_factory(name="kubectl", version="1.0.0"),
        blueprint_factory(name="kubectl", version="1.0.1"),
        blueprint_factory(name="kubectl", version="1.0.0", architecture="armhf"),
        blueprint_factory(name="kubectl", version="1.0.0", revision="2"),
        blueprint_factory(name="kustomize", version="1.0.0", epoch=1),
    ]

    # When
    state_delta = compute_state_delta(packages, blueprints)

    # Then
    assert set(state_delta.added) == {
        DebianRepositoryPackage("kustomize", "1:1.0.0-1~ops2deb", "amd64"),
        DebianRepositoryPackage("kubectl", "1.0.1-1~ops2deb", "amd64"),
        DebianRepositoryPackage("kubectl", "1.0.0-1~ops2deb", "armhf"),
        DebianRepositoryPackage("kubectl", "1.0.0-2~ops2deb", "amd64"),
    }


def test_compute_state_delta__contains_removed_packages(
    blueprint_factory,
):
    # Given
    packages = [
        DebianRepositoryPackage("kubectl", "1.0.0-1~ops2deb", "amd64"),
        DebianRepositoryPackage("kubectl", "1.0.0-1~ops2deb", "armhf"),
    ]
    blueprints = [blueprint_factory(name="kubectl", version="1.0.0")]

    # When
    state_delta = compute_state_delta(packages, blueprints)

    # Then
    assert set(state_delta.removed) == {
        DebianRepositoryPackage("kubectl", "1.0.0-1~ops2deb", "armhf"),
    }
