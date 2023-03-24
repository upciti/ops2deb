![cicd](https://github.com/upciti/ops2deb/actions/workflows/cicd.yml/badge.svg)
[![codecov](https://codecov.io/gh/upciti/ops2deb/branch/main/graph/badge.svg)](https://codecov.io/gh/upciti/ops2deb)
[![MIT license](https://img.shields.io/badge/License-MIT-blue.svg)](https://lbesson.mit-license.org/)
[![Generic badge](https://img.shields.io/badge/type_checked-mypy-informational.svg)](https://mypy.readthedocs.io/en/stable/introduction.html)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/ambv/black)
[![PyPI version shields.io](https://img.shields.io/pypi/v/ops2deb.svg)](https://pypi.python.org/pypi/ops2deb/)
[![Downloads](https://pepy.tech/badge/ops2deb/month)](https://pepy.tech/project/ops2deb)
[![WakeMeOps](https://docs.wakemeops.com/badges/ops2deb.svg)](https://docs.wakemeops.com/packages/ops2deb)

# ops2deb

Are you tired of checking if your favorite devops tools are up-to-date? Are you using a debian based GNU/Linux distribution?
`ops2deb` is designed to generate Debian packages for common devops tools such as kubectl, kustomize, helm, ...,
but can be used to package any portable application. In short, it consumes a configuration file and outputs `.deb` packages.
`ops2deb` can also track new releases of upstream applications and automatically bump application versions in its configuration file.

- [Installation](#installation)
  - [With <a href="https://docs.wakemeops.com" rel="nofollow">wakemeops</a>](#with-wakemeops)
  - [With <a href="https://github.com/pipxproject/pipx">pipx</a>](#with-pipx)
- [Dependencies](#dependencies)
- [Getting started](#getting-started)
- [Usage examples](#usage-examples)
  - [Packaging kubectl](#packaging-kubectl)
  - [Creating a metapackage](#creating-a-metapackage)
  - [Packaging ops2deb with ops2deb](#packaging-ops2deb-with-ops2deb)
  - [Building packages for multiple architectures at once](#building-packages-for-multiple-architectures-at-once)
  - [Using environment variables](#using-environment-variables)
- [Configuration file](#configuration-file)
- [Development](#development)
- [Important notes](#important-notes)
- [Migration guides](#migration-guides)
  - [Migrating to v1](#migrating-to-v1)
  - [Breaking changes in v2](#breaking-changes-in-v2)

## Installation

### With [wakemeops](https://docs.wakemeops.com)

```shell
sudo apt-get install ops2deb
```

### With [pipx](https://github.com/pipxproject/pipx)

```shell
pipx install ops2deb
```

## Dependencies

- Python >= 3.10 if installed with `pip` or `pipx`
- To build debian packages with `ops2deb build` you need the following packages on your host:

```shell
sudo apt install build-essential fakeroot debhelper
```

If you plan to build packages for `armhf` and `arm64` you will also need the following packages:

```shell
sudo apt install binutils-aarch64-linux-gnu binutils-arm-linux-gnueabihf
```

## Getting started

In a test directory run:

```shell
curl https://raw.githubusercontent.com/upciti/ops2deb/main/ops2deb.yml
ops2deb lock # generate lockfile where downloaded file hashes are stored
ops2deb  # equivalent to ops2deb generate && ops2deb build
```

To check for new releases run:

```shell
ops2deb update
```

This command updates each blueprint in the `ops2deb.yml` configuration file with the latest version of the upstream application.

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

### Packaging `kubectl`

The `fetch` field tells ops2deb to download a file. `ops2deb` will check the hash
of downloaded files against a lockfile. To generate/update this lockfile, run
`ops2dbe lock`. By default, the lockfile is named `ops2deb.lock.yml`.

```yaml
name: kubectl
version: 1.20.1
summary: command line client for controlling a Kubernetes cluster
description: |
  kubectl is a command line client for running commands against Kubernetes clusters.
fetch: https://storage.googleapis.com/kubernetes-release/release/v{{version}}/bin/linux/amd64/kubectl
install:
  - kubectl:/usr/bin/
```

### Creating a metapackage

Ops2deb can be used to create [metapackages](https://www.debian.org/blends/hamradio/get/metapackages):

```yaml
name: allthethings
version: 0.1.9
architecture: all
summary: install various devops tools
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
summary: debian packaging tool for portable applications
description: |-
  Ops2deb is primarily designed to easily generate Debian packages for portable
  applications such as single binary applications and scripts. Packages are
  described using a simple configuration file format. Ops2deb can track new
  releases of upstream applications and automatically bump application versions
  in its configuration file.
script:
  - poetry install -E pyinstaller
  - poetry run task single_binary_application
  - install -m 755 build/x86_64-unknown-linux-gnu/release/install/ops2deb {{src}}/usr/bin/
```

### Building packages for multiple architectures at once

If the upstream application is released for multiple architectures,
use the `matrix` object to generate one source package for each architecture:

```yaml
name: helm
matrix:
  architectures:
    - amd64
    - armhf
    - arm64
version: 3.7.2
homepage: https://helm.sh/
summary: Kubernetes package manager
description: |-
  Tool for managing Kubernetes charts.
  Charts are packages of pre-configured Kubernetes resources.
depends:
  - kubectl
fetch: https://get.helm.sh/helm-v{{version}}-linux-{{goarch}}.tar.gz
script:
  - mv linux-*/helm {{src}}/usr/bin/
```

The blueprint above will generate three packages: `helm_3.7.2-1~ops2deb_armhf.deb`, `helm_3.7.2-1~ops2deb_arm64.deb` and `helm_3.7.2-1~ops2deb_amd64.deb`

Note the use of the `{{goarch}}` variable which maps debian architectures to sensible go architectures.

You can also define your own architecture maps using the `fetch.targets` field and the `{{target}}` jinja variable:

```yaml
name: bottom
matrix:
  architectures:
    - amd64
    - armhf
version: 0.6.6
revision: 2
homepage: https://clementtsang.github.io/bottom
summary: cross-platform graphical process/system monitor
description: |-
  A cross-platform graphical process/system monitor with a customizable interface
  and a multitude of features. Supports Linux, macOS, and Windows.
  Inspired by gtop, gotop, and htop.
fetch:
  url: https://github.com/ClementTsang/bottom/releases/download/{{version}}/bottom_{{target}}.tar.gz
  targets:
    amd64: x86_64-unknown-linux-gnu
    armhf: armv7-unknown-linux-gnueabihf
install:
  - btm:/usr/bin/
```

### Using environment variables

You can use `{{env("VARIABLE", "a_default")}}` in all fields except `fetch.targets.*`.
The example below uses environment variables set by Gitlab CI:

```yaml
name: "{{env('CI_PROJECT_NAME')}}"
version: "{{env('CI_COMMIT_TAG', '0')}}"
homepage: "{{env('CI_PROJECT_URL')}}"
summary: awesome application for doing things
description: |-
  Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor
  incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis
  nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.
script:
  - install -m 755 build/x86_64-unknown-linux-gnu/release/install/great-app {{src}}/usr/bin/
```

## Configuration file

Written in YAML and composed of a single blueprint object or a list of blueprints objects. A blueprint is defined by the following:

| Field          | Meaning                                                                                              | Default |
| -------------- | ---------------------------------------------------------------------------------------------------- | ------- |
| `name`         | Component name, e.g. `kustomize`.                                                                    |         |
| `matrix`       | Generate multiple packages from a single blueprint.                                                  | `None`  |
| `version`      | Application release to package.                                                                      |         |
| `revision`     | Package revistion.                                                                                   | `1`     |
| `epoch`        | Package epoch.                                                                                       | `0`     |
| `architecture` | Package architecture.                                                                                | `amd64` |
| `homepage`     | Upstream project homepage.                                                                           | `None`  |
| `summary`      | Package short description.                                                                           |         |
| `description`  | Package full description.                                                                            |         |
| `depends`      | List of package dependencies. Corresponds to `Depends` entry in `debian/control`.                    | `[]`    |
| `recommends`   | List of package recommended dependencies. Corresponds to `Recommends` entry in `debian/control`.     | `[]`    |
| `conflicts`    | List of conflicting packages. Corresponds to `Conflicts` entry in `debian/control`.                  | `[]`    |
| `fetch`        | A file to download. `tar.gz`, `tar.xz`, `tar`, `zip` and `deb` archives are extracted automatically. | `None`  |
| `install`      | List of here-documents and files/directories to add to the debian package.                           | `[]`    |
| `script`       | List of build instructions templated with jinja2 and interpreted with the default `shell`.            | `[]`    |

## Development

You will need [poetry](https://python-poetry.org/), and probably [pyenv](https://github.com/pyenv/pyenv) if you don't have python 3.10 on your host.

```shell
poetry install
```

To run ops2deb test suite run:

```shell
poetry run task check
```

To build a python wheel:

```shell
poetry run poetry build
```

Note that the `poetry run` is important to enable [poetry-dynamic-versioning](https://github.com/mtkennerly/poetry-dynamic-versioning)
which is installed as a dev dependency.

To build a single binary application:

Install required build dependencies:

```shell
sudo apt install binutils python3-dev
poetry install -E pyinstaller
```

And run:

```shell
poetry run task single_binary_application
```

## Important notes

`ops2deb` **DOES NOT** sandbox build instructions so if you do something like:

```shell
script:
- rm -rf ~/*
```

You will loose your files... To make sure that you won't mess with your system, run it within a container.

## Migration guides

### Migrating to v1

Lockfile `ops2deb.lock.yml` was introduced in ops2deb v1.0.0, before that downloaded file hashes where stored in the configuration file, in the blueprint `fetch` object.

To migrate from ops2deb <= 1.0.3 to ops2deb > 1.0.3:

- Install ops2deb 1.0.3
- Run `ops2deb migrate`

### Breaking changes in v2

- `GITHUB_TOKEN` environment variable renamed to `OPS2DEB_GITHUB_TOKEN`
- Command line argument `-k` was removed. Start `ops2deb.yml` with `# lockfile={path_to_lockfile}` to override the default lockfile path.
