import json
from pathlib import Path
from textwrap import wrap
from typing import Any, Callable, Optional, Tuple

import yaml

from ops2deb.exceptions import Ops2debFormatterError
from ops2deb.parser import Blueprint, load, validate
from ops2deb.utils import PrettyYAMLDumper


def sort_blueprints(blueprints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(blueprint: dict[str, Any]) -> Tuple[str, str, int]:
        return blueprint["name"], blueprint["version"], blueprint.get("revision", 1)

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
    blueprint = json.loads(Blueprint.construct(**blueprint).json(exclude_defaults=True))
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


def format(
    configuration_path: Path,
    additional_blueprint_formatting: Optional[Callable[[dict[str, Any]], None]] = None,
) -> None:
    configuration_dict = load(configuration_path)
    validate(configuration_dict)

    # configuration file can be a list of blueprints or a single blueprint
    raw_blueprints = (
        configuration_dict
        if isinstance(configuration_dict, list)
        else [configuration_dict]
    )

    # sort blueprints by name, version and revision
    raw_blueprints = sort_blueprints(raw_blueprints)

    if additional_blueprint_formatting is not None:
        for blueprint in raw_blueprints:
            additional_blueprint_formatting(blueprint)

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

    # save formatted configuration file
    original_configuration_content = configuration_path.read_bytes()
    formatted_configuration_content = b"\n".join(new_yaml_dump_lines)
    configuration_path.write_bytes(formatted_configuration_content)

    if formatted_configuration_content != original_configuration_content:
        raise Ops2debFormatterError(f"Reformatted {configuration_path}")
