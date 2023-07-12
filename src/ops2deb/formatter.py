import json
from textwrap import wrap
from typing import Any, OrderedDict, Tuple

import yaml
from semver.version import Version

from ops2deb.exceptions import Ops2debFormatterError
from ops2deb.parser import (
    Blueprint,
    ConfigurationFile,
    Resources,
    get_default_lockfile_path,
)
from ops2deb.utils import PrettyYAMLDumper


def sort_blueprints(blueprints: list[OrderedDict[str, Any]]) -> list[dict[str, Any]]:
    def key(blueprint: dict[str, Any]) -> Tuple[str, Version, int]:
        try:
            version_str = blueprint["matrix"]["versions"][-1]
        except KeyError:
            version_str = blueprint["version"]
        version: Version = Version(0, 0, 0)
        if Version.is_valid(version_str):
            version = Version.parse(version_str)
        revision_str = blueprint.get("revision", "1")
        try:
            revision = int(revision_str)
        except ValueError:
            revision = 1
        return blueprint["name"], version, revision

    return sorted(blueprints, key=key)


def format_description(description: str) -> str:
    lines: list[str] = []
    description = description.strip("\n ")
    for line in description.split("\n"):
        lines.extend(wrap(line, width=79) or [""])
    return "\n".join(lines)


def format_blueprint(blueprint: dict[str, Any]) -> dict[str, Any]:
    if blueprint_arch := blueprint.pop("arch", None):
        blueprint["architecture"] = blueprint_arch
    blueprint = json.loads(
        Blueprint.model_validate(blueprint).model_dump_json(exclude_defaults=True)
    )
    if (blueprint_fetch := blueprint.get("fetch", None)) and len(blueprint_fetch) == 1:
        blueprint["fetch"] = blueprint_fetch["url"]
    if blueprint_desc := blueprint.get("description", None):
        blueprint["description"] = format_description(blueprint_desc)
    keys_to_remove: list[str] = []
    for key, value in blueprint.items():
        if isinstance(value, list) and not blueprint.get(key):
            keys_to_remove.append(key)
    for key in keys_to_remove:
        blueprint.pop(key, None)
    return blueprint


def format_configuration_file(configuration: ConfigurationFile) -> bool:
    # sort blueprints by name, version and revision
    raw_blueprints = sort_blueprints(configuration.raw_blueprints)

    # wrap descriptions, remove default values, remove empty lists
    raw_blueprints = [format_blueprint(b) for b in raw_blueprints]

    # dump to yaml, use | for multiline strings and double quotes instead of single quotes
    yaml_dump = yaml.dump(
        raw_blueprints if len(raw_blueprints) > 1 else raw_blueprints[0],
        Dumper=PrettyYAMLDumper,
        default_flow_style=False,
        sort_keys=False,
        encoding="utf-8",
    )

    # add line break between blueprints
    yaml_dump_lines = yaml_dump.split(b"\n")
    new_yaml_dump_lines: list[bytes] = [yaml_dump_lines[0]]
    for line in yaml_dump_lines[1:]:
        if line.startswith(b"- "):
            new_yaml_dump_lines.append(b"")
        new_yaml_dump_lines.append(line)

    # re-add lockfile path if needed
    lockfile_path = configuration.lockfile_path
    if lockfile_path != get_default_lockfile_path(configuration.path):
        relative_lockfile_path = lockfile_path.relative_to(configuration.path.parent)
        new_yaml_dump_lines.insert(0, f"# lockfile={relative_lockfile_path}".encode())
        new_yaml_dump_lines.insert(1, b"")

    # save formatted configuration file
    original_configuration_content = configuration.path.read_bytes()
    formatted_configuration_content = b"\n".join(new_yaml_dump_lines)
    configuration.path.write_bytes(formatted_configuration_content)

    return formatted_configuration_content != original_configuration_content


def format_all(resources: Resources) -> None:
    formatted_configuration_files: list[str] = []
    for configuration in resources.configuration_files:
        if format_configuration_file(configuration) is True:
            formatted_configuration_files.append(str(configuration.path))
    if formatted_configuration_files:
        message: str = "Formatted file(s): " + ", ".join(formatted_configuration_files)
        raise Ops2debFormatterError(message)
