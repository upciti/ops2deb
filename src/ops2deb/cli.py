import shutil
import sys
import traceback
from pathlib import Path
from typing import Any, List, NoReturn, Optional, Tuple

import click
import typer

from . import __version__, builder, formatter, generator, logger, parser, updater
from .exceptions import Ops2debError
from .fetcher import DEFAULT_CACHE_DIRECTORY, Fetcher


class DefaultCommandGroup(click.Group):
    """
    Make it so that calling ops2deb without a subcommand
    is equivalent to calling the default subcommand.
    """

    def __init__(self, *args: Any, **kwargs: Any):
        self.default_command = "default"
        self.ignore_unknown_options = True
        super().__init__(*args, **kwargs)

    def parse_args(self, ctx: click.Context, args: List[str]) -> List[str]:
        if not args:
            args.insert(0, self.default_command)
        return super().parse_args(ctx, args)

    def get_command(self, ctx: click.Context, cmd_name: str) -> Optional[click.Command]:
        if cmd_name.startswith("-") and cmd_name not in self.commands:
            cmd_name = self.default_command
            ctx.default_command = True  # type: ignore
        return super().get_command(ctx, cmd_name)

    def resolve_command(
        self, ctx: click.Context, args: List[str]
    ) -> Tuple[Optional[str], Optional[click.Command], List[str]]:
        cmd_name, cmd, args = super().resolve_command(ctx, args)
        if hasattr(ctx, "default_command") and cmd_name:
            args.insert(0, cmd_name)
        return cmd_name, cmd, args


def validate_exit_code(exit_code: int) -> int:
    if exit_code > 255 or exit_code < 0:
        raise typer.BadParameter("Invalid exit code")
    return exit_code


def error(exception: Exception, exit_code: int) -> NoReturn:
    logger.error(str(exception))
    logger.debug(traceback.format_exc())
    sys.exit(exit_code)


option_verbose: bool = typer.Option(
    False,
    "--verbose",
    "-v",
    envvar="OPS2DEB_VERBOSE",
    help="Enable more logs.",
    callback=lambda v: logger.enable_debug(v),
)

option_exit_code: int = typer.Option(
    1,
    "--exit-code",
    "-e",
    envvar="OPS2DEB_EXIT_CODE",
    help="Exit code to use in case of failure.",
    callback=validate_exit_code,
)

option_configuration = typer.Option(
    "ops2deb.yml",
    "--config",
    "-c",
    envvar="OPS2DEB_CONFIG",
    help="Path to configuration file.",
)

option_cache_directory: Path = typer.Option(
    DEFAULT_CACHE_DIRECTORY,
    "--cache-dir",
    envvar="OPS2DEB_CACHE_DIR",
    help="Directory where files specified in fetch instructions are downloaded.",
)

option_output_directory: Path = typer.Option(
    "output",
    "--output-dir",
    "-o",
    envvar="OPS2DEB_OUTPUT_DIR",
    help="Directory where debian source packages are generated and built.",
)

option_debian_repository: str = typer.Option(
    None,
    "--repository",
    "-r",
    envvar="OPS2DEB_REPOSITORY",
    help='Format: "{debian_repo_url} {distribution_name}". '
    'Example: "http://deb.wakemeops.com/ stable". '
    "Packages already published in the repo won't be generated.",
)

option_workers_count: int = typer.Option(
    4,
    "--workers",
    "-w",
    envvar="OPS2DEB_WORKERS_COUNT",
    help="Max number of source packages to build in parallel",
)


app = typer.Typer(cls=DefaultCommandGroup)


@app.command(help="Generate and build source packages.")
def default(
    verbose: bool = option_verbose,
    exit_code: int = option_exit_code,
    configuration_path: Path = option_configuration,
    output_directory: Path = option_output_directory,
    cache_directory: Path = option_cache_directory,
    debian_repository: str = option_debian_repository,
    workers_count: int = option_workers_count,
) -> None:
    Fetcher.set_cache_directory(cache_directory)
    try:
        blueprints = parser.parse(configuration_path)
        packages = generator.generate(blueprints, output_directory, debian_repository)
        builder.build([p.package_directory for p in packages], workers_count)
    except Ops2debError as e:
        error(e, exit_code)


@app.command(help="Generate debian source packages from configuration file.")
def generate(
    verbose: bool = option_verbose,
    exit_code: int = option_exit_code,
    configuration_path: Path = option_configuration,
    output_directory: Path = option_output_directory,
    cache_directory: Path = option_cache_directory,
    debian_repository: str = option_debian_repository,
) -> None:
    Fetcher.set_cache_directory(cache_directory)
    try:
        blueprints = parser.parse(configuration_path)
        generator.generate(blueprints, output_directory, debian_repository)
    except Ops2debError as e:
        error(e, exit_code)


@app.command(help="Build debian packages from source packages.")
def build(
    verbose: bool = option_verbose,
    exit_code: int = option_exit_code,
    output_directory: Path = option_output_directory,
    workers_count: int = option_workers_count,
) -> None:
    try:
        builder.build_all(output_directory, workers_count)
    except Ops2debError as e:
        error(e, exit_code)


@app.command(help="Clear ops2deb download cache.")
def purge(cache_directory: Path = option_cache_directory) -> None:
    shutil.rmtree(cache_directory, ignore_errors=True)


@app.command(help="Look for new application releases and edit configuration file.")
def update(
    verbose: bool = option_verbose,
    exit_code: int = option_exit_code,
    configuration_path: Path = option_configuration,
    cache_directory: Path = option_cache_directory,
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
    Fetcher.set_cache_directory(cache_directory)
    try:
        updater.update(configuration_path, dry_run, output_path)
    except Ops2debError as e:
        error(e, exit_code)


@app.command(help="Validate configuration file.")
def validate(
    verbose: bool = option_verbose,
    exit_code: int = option_exit_code,
    configuration_path: Path = option_configuration,
) -> None:
    try:
        parser.parse(configuration_path)
    except Ops2debError as e:
        error(e, exit_code)


@app.command(help="Format configuration file.")
def format(
    verbose: bool = option_verbose,
    exit_code: int = option_exit_code,
    configuration_path: Path = option_configuration,
) -> None:
    try:
        formatter.format(configuration_path)
    except Ops2debError as e:
        error(e, exit_code)


@app.command(help="Output ops2deb version.")
def version() -> None:
    logger.info(__version__)


def main() -> None:
    app()
