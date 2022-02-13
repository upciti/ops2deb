import json
from pathlib import Path
from textwrap import wrap
from typing import Any, Dict, List, Tuple

import yaml

from .exceptions import Ops2debFormatterError
from .parser import Blueprint, load, validate


class PrettyYAMLDumper(yaml.dumper.SafeDumper):
    def expect_block_sequence(self) -> None:
        self.increase_indent(flow=False, indentless=False)
        self.state = self.expect_first_block_sequence_item

    def choose_scalar_style(self) -> str:
        style = super().choose_scalar_style()
        style = '"' if style == "'" else style
        style = "|" if self.analysis.multiline else style
        return style


def sort_blueprints(blueprints: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def key(blueprint: Dict[str, Any]) -> Tuple[str, str, int]:
        return blueprint["name"], blueprint["version"], blueprint.get("revision", 1)

    return sorted(blueprints, key=key)


def format_description(description: str) -> str:
    lines: List[str] = []
    description = description.strip("\n ")
    for line in description.split("\n"):
        lines.extend(wrap(line, width=79) or [""])
    return "\n".join(lines)


def format_blueprint(blueprint: Dict[str, Any]) -> Dict[str, Any]:
    blueprint = json.loads(Blueprint.construct(**blueprint).json(exclude_defaults=True))
    blueprint["description"] = format_description(blueprint["description"])
    for key in "depends", "recommends", "script", "conflicts", "install":
        if not blueprint.get(key):
            blueprint.pop(key, None)
    return blueprint


def format(configuration_path: Path) -> None:
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
    new_yaml_dump_lines: List[bytes] = [yaml_dump_lines[0]]
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
