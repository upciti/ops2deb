import sys
import traceback
from pathlib import Path
from typing import NoReturn

import typer

from . import builder, generator, updater
from .fetcher import purge_cache
from .parser import parse
from .settings import settings

app = typer.Typer()


def error(exception: Exception) -> NoReturn:
    typer.secho(str(exception), fg=typer.colors.RED, err=True)
    if settings.verbose:
        typer.secho(traceback.format_exc(), fg=typer.colors.BRIGHT_BLACK, err=True)
    sys.exit(1)


@app.command(help="Generate debian source packages")
def generate() -> None:
    try:
        generator.generate(parse(settings.config).__root__)
    except Exception as e:
        error(e)


@app.command(help="Build debian source packages")
def build() -> None:
    try:
        builder.build(settings.work_dir)
    except Exception as e:
        error(e)


@app.command(help="Clear ops2deb download cache")
def purge() -> None:
    purge_cache()


@app.command(help="Look for new application releases")
def update(dry_run: bool = typer.Option(False, "--dry-run", "-d")) -> None:
    try:
        sys.exit(not updater.update(settings.config, dry_run))
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
