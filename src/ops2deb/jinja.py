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

# Allow users to use environment variables in blueprints
functions = {
    "env": lambda x, y=None: os.environ.get(x, y),
}

filters = {
    "goarch": lambda arch: DEFAULT_GOARCH_MAP.get(arch, None),
    "rust_target": lambda arch: DEFAULT_RUST_TARGET_MAP.get(arch, None),
}

environment = Environment()
environment.globals.update(functions)
environment.filters.update(filters)
