import sys
import traceback
from pathlib import Path
from typing import NoReturn

import typer

from .builder import build
from .fetcher import purge_cache
from .generator import generate
from .parser import parse
from .settings import settings
from .updater import update

app = typer.Typer()


def error(exception: Exception) -> NoReturn:
    typer.secho(str(exception), fg=typer.colors.RED, err=True)
    if settings.verbose:
        typer.secho(traceback.format_exc(), fg=typer.colors.BRIGHT_BLACK, err=True)
    sys.exit(1)


@app.command(name="generate", help="Generate debian source packages")
def generate_packages() -> None:
    try:
        generate(parse(settings.config).__root__)
    except Exception as e:
        error(e)


@app.command(name="build", help="Build debian source packages")
def build_packages() -> None:
    try:
        build(settings.work_dir)
    except Exception as e:
        error(e)


@app.command(help="Clear ops2deb download cache")
def purge() -> None:
    purge_cache()


@app.command(name="update", help="Look for new application releases")
def update_applications() -> None:
    try:
        update(parse(settings.config).__root__)
    except Exception as e:
        error(e)


@app.callback()
def args_cb(
    verbose: bool = typer.Option(settings.verbose, "--verbose", "-v"),
    config: Path = typer.Option(settings.config, "--config", "-c"),
    work_dir: Path = typer.Option(settings.work_dir, "--work-dir", "-w"),
) -> None:
    settings.verbose = verbose
    settings.config = config
    settings.work_dir = work_dir


def main() -> None:
    app()


if __name__ == "__main__":
    main()
