from typer import colors, secho

_debug: bool = False


def enable_debug(enable: bool) -> None:
    global _debug
    _debug = enable


def info(message: str) -> None:
    secho(message)


def debug(message: str) -> None:
    if _debug is True:
        secho(message, fg=colors.BRIGHT_BLACK, err=True)


def warning(message: str) -> None:
    secho(message, fg=colors.YELLOW, err=True)


def error(message: str) -> None:
    secho(message, fg=colors.RED, err=True)


def title(message: str) -> None:
    secho(message, fg=colors.BLUE, bold=True)
