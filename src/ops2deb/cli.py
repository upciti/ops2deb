import shutil
import sys
import traceback
from pathlib import Path
from typing import Any, List, NoReturn, Optional

import click
import typer
from rich.console import Console
from rich.table import Table
from typer.core import TyperGroup

from ops2deb import __version__, generator, logger, updater
from ops2deb.apt import list_repository_packages
from ops2deb.builder import build_source_packages, find_and_build_source_packages
from ops2deb.delta import StateDelta, compute_state_delta
from ops2deb.exceptions import Ops2debError
from ops2deb.fetcher import DEFAULT_CACHE_DIRECTORY, Fetcher
from ops2deb.formatter import format_all
from ops2deb.parser import Resources, load_resources


class DefaultCommandGroup(TyperGroup):
    """
    Make it so that calling ops2deb without a subcommand
    is equivalent to calling the default subcommand.
    """

    def __init__(self, *args: Any, **kwargs: Any):
        self.default_command = "default"
        self.ignore_unknown_options = True
        super().__init__(*args, **kwargs)

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if not args:
            args.insert(0, self.default_command)
        return super().parse_args(ctx, args)

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        if cmd_name.startswith("-") and cmd_name not in self.commands:
            cmd_name = self.default_command
            ctx.default_command = True  # type: ignore
        return super().get_command(ctx, cmd_name)

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        cmd_name, cmd, args = super().resolve_command(ctx, args)
        if hasattr(ctx, "default_command") and cmd_name:
            args.insert(0, cmd_name)
        return cmd_name, cmd, args


def validate_exit_code(exit_code: int) -> int:
    if exit_code > 255 or exit_code < 0:
        raise typer.BadParameter("Invalid exit code")
    return exit_code


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

option_configurations_search_pattern: str = typer.Option(
    "./**/ops2deb.yml",
    "--config",
    "-c",
    envvar="OPS2DEB_CONFIG",
    help="Path to configuration file or glob pattern.",
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

option_debian_repository: Optional[str] = typer.Option(
    None,
    "--repository",
    "-r",
    envvar="OPS2DEB_REPOSITORY",
    help='Format: "{debian_repo_url} {distribution_name}". '
    'Example: "http://deb.wakemeops.com/ stable". '
    "Packages already published in the repo won't be generated.",
)

option_only: Optional[List[str]] = typer.Option(
    None,
    "--only",
    envvar="OPS2DEB_ONLY_BLUEPRINTS",
    help="Only blueprints with matching names will be taken into account",
)

option_workers_count: int = typer.Option(
    4,
    "--workers",
    "-w",
    envvar="OPS2DEB_WORKERS_COUNT",
    help="Max number of source packages to build in parallel",
)


app = typer.Typer(cls=DefaultCommandGroup)


def error(exception: Exception, exit_code: int) -> NoReturn:
    logger.error(str(exception))
    logger.debug(traceback.format_exc())
    sys.exit(exit_code)


def print_loaded_resources(resources: Resources) -> None:
    logger.title(
        f"Loaded {len(resources.configuration_files)} configuration file(s) and "
        f"{len(resources.blueprints)} blueprint(s)"
    )


def print_state_delta_as_rich_table(state_delta: StateDelta) -> None:
    table = Table(box=None, pad_edge=False, show_header=False)
    for package in state_delta.removed:
        table.add_row("[red]-[/]", package.name, package.version, package.architecture)
    for package in state_delta.added:
        table.add_row("[green]+[/]", package.name, package.version, package.architecture)
    console = Console(stderr=True)
    console.print(table)


@app.command(help="Generate and build source packages.")
def default(
    verbose: bool = option_verbose,
    exit_code: int = option_exit_code,
    configurations_search_pattern: str = option_configurations_search_pattern,
    output_directory: Path = option_output_directory,
    cache_directory: Path = option_cache_directory,
    debian_repository: Optional[str] = option_debian_repository,
    only: Optional[List[str]] = option_only,
    workers_count: int = option_workers_count,
) -> None:
    try:
        resources = load_resources(configurations_search_pattern)
        print_loaded_resources(resources)
        fetcher = Fetcher(cache_directory)
        packages = generator.generate(
            resources,
            fetcher,
            output_directory,
            debian_repository,
            only or None,
        )
        build_source_packages([p.package_directory for p in packages], workers_count)
    except Ops2debError as e:
        error(e, exit_code)


@app.command(help="Generate debian source packages from configuration files.")
def generate(
    verbose: bool = option_verbose,
    exit_code: int = option_exit_code,
    configurations_search_pattern: str = option_configurations_search_pattern,
    output_directory: Path = option_output_directory,
    cache_directory: Path = option_cache_directory,
    debian_repository: Optional[str] = option_debian_repository,
    only: Optional[List[str]] = option_only,
) -> None:
    try:
        resources = load_resources(configurations_search_pattern)
        print_loaded_resources(resources)
        fetcher = Fetcher(cache_directory)
        generator.generate(
            resources,
            fetcher,
            output_directory,
            debian_repository,
            only or None,
        )
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
        find_and_build_source_packages(output_directory, workers_count)
    except Ops2debError as e:
        error(e, exit_code)


@app.command(help="Clear ops2deb download cache.")
def purge(cache_directory: Path = option_cache_directory) -> None:
    shutil.rmtree(cache_directory, ignore_errors=True)


@app.command(help="Look for new application releases and edit configuration files.")
def update(
    verbose: bool = option_verbose,
    exit_code: int = option_exit_code,
    configurations_search_pattern: str = option_configurations_search_pattern,
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
    skip: Optional[List[str]] = typer.Option(
        None,
        "-s",
        "--skip",
        envvar="OPS2DEB_SKIP_BLUEPRINTS",
        help="Name of blueprint that should not be updated. Can be used multiple times.",
    ),
    only: Optional[List[str]] = option_only,
    max_versions: int = typer.Option(
        1, "-m", "--max-versions", envvar="OPS2DEB_MAX_VERSIONS"
    ),
) -> None:
    try:
        resources = load_resources(configurations_search_pattern)
        print_loaded_resources(resources)
        fetcher = Fetcher(cache_directory)
        updater.update(
            resources,
            fetcher,
            dry_run,
            output_path,
            skip,
            only,
            max_versions,
        )
    except Ops2debError as e:
        error(e, exit_code)


@app.command(help="Validate configuration files.")
def validate(
    verbose: bool = option_verbose,
    exit_code: int = option_exit_code,
    configurations_search_pattern: str = option_configurations_search_pattern,
) -> None:
    try:
        load_resources(configurations_search_pattern)
    except Ops2debError as e:
        error(e, exit_code)


@app.command(help="Format configuration files.")
def format(
    verbose: bool = option_verbose,
    exit_code: int = option_exit_code,
    configurations_search_pattern: str = option_configurations_search_pattern,
) -> None:
    try:
        resources = load_resources(configurations_search_pattern)
        print_loaded_resources(resources)
        format_all(resources)
    except Ops2debError as e:
        error(e, exit_code)


@app.command(help="Output ops2deb version.")
def version() -> None:
    logger.info(__version__)


@app.command(help="Update lock files.")
def lock(
    verbose: bool = option_verbose,
    exit_code: int = option_exit_code,
    configurations_search_pattern: str = option_configurations_search_pattern,
    cache_directory: Path = option_cache_directory,
) -> None:
    try:
        fetcher = Fetcher(cache_directory)
        resources = load_resources(configurations_search_pattern)
        print_loaded_resources(resources)
        for blueprint in resources.blueprints:
            lock_file = resources.get_blueprint_lock(blueprint)
            for url in blueprint.render_fetch_urls():
                if url not in lock_file:
                    fetcher.add_task(url, data=lock_file)
        for result in fetcher.run_tasks()[0]:
            result.task_data.add([result])
        resources.save()
    except Ops2debError as e:
        error(e, exit_code)


@app.command(help="Output state drift configuration files and debian repository")
def delta(
    verbose: bool = option_verbose,
    exit_code: int = option_exit_code,
    debian_repository: Optional[str] = option_debian_repository,
    configurations_search_pattern: str = option_configurations_search_pattern,
    output_as_json: bool = typer.Option(
        False, "--json", help="Output state delta as a JSON"
    ),
) -> None:
    if debian_repository is None:
        logger.error("Missing command line option --repository")
        sys.exit(exit_code)
    try:
        resources = load_resources(configurations_search_pattern)
        packages = list_repository_packages(debian_repository)
        state_delta = compute_state_delta(packages, resources.blueprints)
        print_state_delta_as_rich_table(state_delta)
        if output_as_json:
            print(state_delta.model_dump_json(indent=2))
    except Ops2debError as e:
        error(e, exit_code)


def main() -> None:
    app()
