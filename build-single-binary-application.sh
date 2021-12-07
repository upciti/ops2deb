#!/usr/bin/env bash

set -e

if ! which poetry pyoxidizer make gcc; then
  echo "Some dependencies are missing, please install"
  echo "poetry, pyoxidizer, make and gcc"
  exit 1
fi

# install project dependencies in .venv
POETRY_VIRTUALENVS_IN_PROJECT=true poetry install --no-dev

# get installed version of pydantic
readonly pydantic_version=$(poetry run python -c "import pkg_resources; print(pkg_resources.get_distribution('pydantic').version)")

# force installation of pydantic pure python implementation
# to avoid 40MB of dependencies on cython shared libs
readonly pydantic_wheel_url=https://files.pythonhosted.org/packages/py3/p/pydantic/pydantic-$pydantic_version-py3-none-any.whl
poetry run pip install --force $pydantic_wheel_url

# build single binary application with pyoxidizer
pyoxidizer build --release

# ops2deb must work without lib directory which normally
# contains shared libs dependencies
rm -rf build/x86_64-unknown-linux-gnu/release/install/lib

# test ops2deb help cli
build/x86_64-unknown-linux-gnu/release/install/ops2deb --help
