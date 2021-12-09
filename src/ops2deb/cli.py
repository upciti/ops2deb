import shutil
import sys
import traceback
from pathlib import Path
from typing import NoReturn, Optional

import typer

from . import builder, fetcher, generator, logger, parser, updater
from .exceptions import Ops2debError

# Options below are used by multiple subcommands
OPTION_CONFIGURATION = typer.Option(
    "ops2deb.yml",
    "--config",
    "-c",
    envvar="OPS2DEB_CONFIG",
    help="Path to configuration file.",
)
OPTION_CACHE_DIRECTORY: Path = typer.Option(
    fetcher.DEFAULT_CACHE_DIRECTORY,
    "--cache-dir",
    envvar="OPS2DEB_CACHE_DIR",
    help="Directory where files specified in fetch instructions are downloaded.",
)
OPTION_OUTPUT_DIRECTORY: Path = typer.Option(
    "output",
    "--output-dir",
    "-o",
    envvar="OPS2DEB_OUTPUT_DIR",
    help="Directory where debian source packages are generated and built.",
)

DEFAULT_EXIT_CODE = 1
_exit_code = DEFAULT_EXIT_CODE

app = typer.Typer()


def error(exception: Exception) -> NoReturn:
    logger.error(str(exception))
    logger.debug(traceback.format_exc())
    sys.exit(_exit_code)


@app.command(help="Generate debian source packages from configuration file.")
def generate(
    configuration_path: Path = OPTION_CONFIGURATION,
    output_directory: Path = OPTION_OUTPUT_DIRECTORY,
    cache_directory: Path = OPTION_CACHE_DIRECTORY,
    debian_repository: str = typer.Option(
        None,
        "--repository",
        "-r",
        envvar="OPS2DEB_REPOSITORY",
        help='Format: "{debian_repo_url} {distribution_name}". '
        'Example: "http://deb.wakemeops.com/ stable". '
        "Packages already published in the repo won't be generated.",
    ),
) -> None:
    fetcher.set_cache_directory(cache_directory)
    try:
        generator.generate(
            parser.parse(configuration_path), output_directory, debian_repository
        )
    except Ops2debError as e:
        error(e)


@app.command(help="Build debian packages from source packages.")
def build(output_directory: Path = OPTION_OUTPUT_DIRECTORY) -> None:
    try:
        builder.build(output_directory)
    except Ops2debError as e:
        error(e)


@app.command(help="Clear ops2deb download cache.")
def purge(cache_directory: Path = OPTION_CACHE_DIRECTORY) -> None:
    shutil.rmtree(cache_directory, ignore_errors=True)


@app.command(help="Look for new application releases and edit configuration file.")
def update(
    config: Path = OPTION_CONFIGURATION,
    cache_directory: Path = OPTION_CACHE_DIRECTORY,
    dry_run: bool = typer.Option(
        False, "--dry-run", "-d", help="Don't edit config file."
    ),
    output_path: Optional[Path] = typer.Option(
        None,
        "--output-file",
        envvar="OPS2DEB_OUTPUT_FILE",
        help="Path to file where to save a summary of updated files.",
    ),
) -> None:
    fetcher.set_cache_directory(cache_directory)
    try:
        updater.update(config, dry_run, output_path)
    except Ops2debError as e:
        error(e)


@app.command(help="Validate configuration file.")
def validate(configuration_path: Path = OPTION_CONFIGURATION) -> None:
    try:
        parser.validate(parser.load(configuration_path))
    except Ops2debError as e:
        error(e)


@app.callback()
def args_cb(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", envvar="OPS2DEB_VERBOSE", help="Enable more logs."
    ),
    exit_code: int = typer.Option(
        DEFAULT_EXIT_CODE,
        "--exit-code",
        "-e",
        envvar="OPS2DEB_EXIT_CODE",
        help="Exit code to use in case of failure.",
    ),
) -> None:
    global _exit_code
    _exit_code = exit_code
    if exit_code > 255 or exit_code < 0:
        raise typer.BadParameter("Invalid exit code")
    logger.enable_debug(verbose)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
