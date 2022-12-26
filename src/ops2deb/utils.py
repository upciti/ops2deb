import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Tuple, TypeVar

import yaml

from ops2deb import logger
from ops2deb.exceptions import Ops2debError


def log_and_raise(exception: Exception) -> None:
    logger.error(str(exception))
    raise exception


T = TypeVar("T")
U = TypeVar("U")


def separate_results_from_errors(
    results_and_errors: dict[U, T | Exception]
) -> Tuple[dict[U, T], dict[U, Ops2debError]]:
    results: dict[U, T] = {}
    errors: dict[U, Ops2debError] = {}
    for key, value in results_and_errors.items():
        if isinstance(value, Ops2debError):
            errors[key] = value
        elif isinstance(value, Exception):
            raise value
        else:
            results[key] = value
    return results, errors


@contextmanager
def working_directory(path: Path) -> Iterator[None]:
    origin = Path().absolute()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(origin)


class PrettyYAMLDumper(yaml.dumper.SafeDumper):
    def expect_block_sequence(self) -> None:
        self.increase_indent(flow=False, indentless=False)
        self.state = self.expect_first_block_sequence_item

    def choose_scalar_style(self) -> str:
        style: str = super().choose_scalar_style()
        style = '"' if style == "'" else style
        style = "|" if self.analysis.multiline else style
        return style
