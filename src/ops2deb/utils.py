import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import yaml
from ruamel.yaml.emitter import Emitter

from ops2deb import logger


def log_and_raise(exception: Exception) -> None:
    logger.error(str(exception))
    raise exception


@contextmanager
def working_directory(path: Path) -> Iterator[None]:
    origin = Path().absolute()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(origin)


class PrettyYAMLDumper(yaml.dumper.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:
        return True

    def expect_block_sequence(self) -> None:
        self.increase_indent(flow=False, indentless=False)
        self.state = self.expect_first_block_sequence_item

    def choose_scalar_style(self) -> str:
        style: str = super().choose_scalar_style()
        style = '"' if style == "'" else style
        style = "|" if self.analysis and self.analysis.multiline else style
        return style


class FixIndentEmitter(Emitter):
    def expect_block_sequence(self) -> None:
        self.increase_indent(flow=False, indentless=False)
        self.state = self.expect_first_block_sequence_item

    def choose_scalar_style(self) -> str:
        style: str = super().choose_scalar_style()
        style = '"' if style == "'" else style
        style = "|" if self.analysis.multiline else style
        return style
