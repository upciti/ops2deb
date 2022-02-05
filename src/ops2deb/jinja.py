import os

from jinja2 import Environment

DEFAULT_GOARCH_MAP = {
    "amd64": "amd64",
    "arm64": "arm64",
    "armhf": "arm",
}

DEFAULT_RUST_TARGET_MAP = {
    "amd64": "x86_64-unknown-linux-gnu",
    "arm64": "aarch64-unknown-linux-gnu",
    "armhf": "arm-unknown-linux-gnueabihf",
}

# Let users to use environment variables in blueprints
functions = {
    "env": lambda variable, default=None: os.environ.get(variable, default),
}

# Let users override goarch and rust_target by writing {{target|rust_target}} in urls
filters = {
    "goarch": lambda arch: DEFAULT_GOARCH_MAP.get(arch, arch),
    "rust_target": lambda arch: DEFAULT_RUST_TARGET_MAP.get(arch, arch),
}

environment = Environment()
environment.globals.update(functions)
environment.filters.update(filters)
