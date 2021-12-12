import json
from operator import attrgetter
from pathlib import Path
from textwrap import wrap
from typing import Any, Dict, List, Set

import yaml

from .parser import Blueprint, parse


class PrettyYAMLDumper(yaml.dumper.SafeDumper):
    def expect_block_sequence(self) -> None:
        self.increase_indent(flow=False, indentless=False)
        self.state = self.expect_first_block_sequence_item

    def choose_scalar_style(self) -> str:
        style = super().choose_scalar_style()
        style = '"' if style == "'" else style
        style = "|" if self.analysis.multiline else style
        return style


def sort_blueprints(blueprints: List[Blueprint]) -> List[Blueprint]:
    return sorted(blueprints, key=attrgetter("name", "version", "revision"))


def format_description(description: str) -> str:
    lines: List[str] = []
    for line in description.split("\n"):
        lines.extend(wrap(line, width=79))
    return "\n".join(lines)


def format_blueprint(blueprint: Blueprint) -> Dict[str, Any]:
    update: Dict[str, str] = {"description": format_description(blueprint.description)}
    blueprint = blueprint.copy(update=update)
    exclude: Set[str] = {
        k for k in {"recommends", "conflicts", "depends"} if not getattr(blueprint, k)
    }
    return json.loads(blueprint.json(exclude_defaults=True, exclude=exclude))


def format(configuration_path: Path) -> None:
    # sort blueprints by name, version and revision
    blueprints = sort_blueprints(parse(configuration_path))

    # wrap descriptions, remove default values, remove empty lists
    formatted_blueprints = [format_blueprint(b) for b in blueprints]

    # dump to yaml, use | for multiline strings and double quotes instead of single quotes
    yaml_dump = yaml.dump(
        formatted_blueprints if len(blueprints) > 1 else formatted_blueprints[0],
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
    configuration_path.write_bytes(b"\n".join(new_yaml_dump_lines))
