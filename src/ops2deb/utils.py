import os
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Tuple, TypeVar, Union

from . import logger
from .exceptions import Ops2debError


def log_and_raise(exception: Exception) -> None:
    logger.error(str(exception))
    raise exception


T = TypeVar("T")
U = TypeVar("U")


def separate_results_from_errors(
    results_and_errors: Dict[U, Union[T, Exception]]
) -> Tuple[Dict[U, T], Dict[U, Ops2debError]]:
    results: Dict[U, T] = {}
    errors: Dict[U, Ops2debError] = {}
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
