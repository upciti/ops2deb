![cicd](https://github.com/upciti/ops2deb/actions/workflows/cicd.yml/badge.svg)
[![codecov](https://codecov.io/gh/upciti/ops2deb/branch/main/graph/badge.svg)](https://codecov.io/gh/upciti/ops2deb)
[![MIT license](https://img.shields.io/badge/License-MIT-blue.svg)](https://lbesson.mit-license.org/)
[![Generic badge](https://img.shields.io/badge/type_checked-mypy-informational.svg)](https://mypy.readthedocs.io/en/stable/introduction.html)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/ambv/black)
[![PyPI version shields.io](https://img.shields.io/pypi/v/ops2deb.svg)](https://pypi.python.org/pypi/ops2deb/)
[![Downloads](https://static.pepy.tech/personalized-badge/ops2deb?period=total&units=international_system&left_color=blue&right_color=green&left_text=Downloads)](https://pepy.tech/project/ops2deb)

# ops2deb

Are you tired of checking if your favorite devops tools are up-to-date? Are you using a debian based GNU/Linux distribution?
`ops2deb` is designed to generate Debian packages for common devops tools such as kubectl, kustomize, helm, ...,
but it could be used to package any statically linked application. In short, it consumes a configuration file and outputs `.deb` packages.

## Configuration file

Written in YAML and composed of a single blueprint object or a list of blueprints objects. A blueprint is defined by the following:

| Field         | Meaning                                                                                                                                     | Default |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | ------- |
| `name`        | Component name, e.g. `kustomize`.                                                                                                           |         |
| `version`     | Application release to package.                                                                                                             |         |
| `homepage`    | Upstream project homepage.                                                                                                                  | `None`  |
| `arch`        | Package architecture.                                                                                                                       | `amd64` |
| `revision`    | Package revistion.                                                                                                                          | `1`     |
| `summary`     | Package short description.                                                                                                                  |         |
| `description` | Package full description.                                                                                                                   |         |
| `fetch`       | A binary to download, and a `sha256` checksum. `tar.gz`, `tar.xz`, `tar` and `zip` (requires `unzip`) archives are extracted automatically. | `Null`  |
| `script`      | List of build instructions templated with jinja2 and intepreted with the default `shell`.                                                   | `[]`    |
| `depends`     | List of package dependencies. Corresponds to `Depends` entry in `debian/control`.                                                           | `[]`    |
| `recommends`  | List of package recommended dependencies. Corresponds to `Recommends` entry in `debian/control`.                                            | `[]`    |
| `conflicts`   | List of conflicting packages. Corresponds to `Conflicts` entry in `debian/control`.                                                         | `[]`    |

Example of a configuration file a single blueprint:

```yaml
name: kubectl
version: 1.20.1
summary: Command line client for controlling a Kubernetes cluster
description: |
  kubectl is a command line client for running commands against Kubernetes clusters.
fetch:
  url: https://storage.googleapis.com/kubernetes-release/release/v{{version}}/bin/linux/amd64/kubectl
  sha256: 3f4b52a8072013e4cd34c9ea07e3c0c4e0350b227e00507fb1ae44a9adbf6785
script:
  - mv kubectl {{src}}/usr/bin/
```

## Dependencies

- Python >= 3.9
- To build debian packages with `ops2deb build` you need the following packages on your host:

```shell
sudo apt install build-essential fakeroot debhelper
```

## Installation

### With [wakemeops](https://docs.wakemeops.com)

```shell
sudo apt-get install ops2deb
```

### With [pipx](https://github.com/pipxproject/pipx)

```shell
pipx install ops2deb
```

## Getting started

In a test directory run:

```shell
curl https://raw.githubusercontent.com/upciti/ops2deb/main/ops2deb.yml
ops2deb generate
ops2deb build
```

To check for new releases run:

```shell
ops2deb update
```

This command updates each blueprint in the `ops2deb.yml` configuration file with the latest version of
the upstream application (currently only works for applications using semantic versioning).

By default `ops2deb` caches downloaded content in `/tmp/ops2deb_cache`:

```shell
tree /tmp/ops2deb_cache
```

The cache can be flushed with:

```shell
ops2deb purge
```

For more information about existing subcommands and options run `ops2deb --help`.

## Usage examples

### Creating a metapackage

Ops2deb can be used to create [metapackages](https://www.debian.org/blends/hamradio/get/metapackages):

```yaml
name: allthethings
version: 0.1.9
arch: all
summary: Install various devops tools
description: Some great description.
depends:
  - kubectl
  - kustomize
  - helm
  - helmfile
  - devspace
```

### Packaging ops2deb with ops2deb

Note that when the fetch key is not used, ops2deb will run the build script from the directory where it was called.
Hence for the following blueprint to succeed, you have to run ops2deb from the root directory of this github project.

```yaml
name: ops2deb
version: 0.15.0
homepage: https://github.com/upciti/ops2deb
summary: Debian packaging tool for portable applications
description: |-
  Ops2deb is primarily designed to easily generate Debian packages for portable
  applications such as single binary applications and scripts. Packages are
  described using a simple configuration file format. Ops2deb can track new
  releases of upstream applications and automatically bump application versions
  in its configuration file.
script:
  - ./build-single-binary-application.sh
  - install -m 755 build/x86_64-unknown-linux-gnu/release/install/ops2deb {{src}}/usr/bin/
```

## Development

You will need [poetry](https://python-poetry.org/), and probably [pyenv](https://github.com/pyenv/pyenv) if you don't have python 3.9 on your host.

```shell
poetry install
```

To run ops2deb test suite run:

```shell
poetry run task check
```

## Important notes

`ops2deb` **DOES NOT** sandbox build instructions so if you do something like:

```shell
script:
- rm -rf ~/*
```

You will loose your files... To make sure that you won't mess with your system, run it within a container.
