import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, List, Tuple, TypeVar, Union

from . import logger
from .exceptions import Ops2debError


def log_and_raise(exception: Exception) -> None:
    logger.error(str(exception))
    raise exception


T = TypeVar("T")


def separate_successes_from_errors(
    items: Iterable[Union[T, Exception]]
) -> Tuple[List[T], List[Ops2debError]]:
    results: List[T] = []
    errors: List[Ops2debError] = []
    for item in items:
        if isinstance(item, Ops2debError):
            errors.append(item)
        elif isinstance(item, Exception):
            raise item
        else:
            results.append(item)
    return results, errors


@contextmanager
def working_directory(path: Path) -> Iterator[None]:
    origin = Path().absolute()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(origin)
