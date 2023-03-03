import glob
import re
from dataclasses import dataclass
from functools import cached_property
from itertools import product
from pathlib import Path
from typing import Any, Literal, OrderedDict, cast

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    Field,
    PrivateAttr,
    ValidationError,
    root_validator,
    validator,
)
from pydantic.fields import ModelField
from ruamel.yaml import YAML, YAMLError  # type: ignore[attr-defined]

from ops2deb import logger
from ops2deb.exceptions import Ops2debParserError
from ops2deb.jinja import DEFAULT_GOARCH_MAP, DEFAULT_RUST_TARGET_MAP, environment
from ops2deb.lockfile import Lock
from ops2deb.utils import FixIndentEmitter

Architecture = Literal["all", "amd64", "arm64", "armhf"]

LOCKFILE_PATH_HEADER_RE = re.compile(r"^# lockfile=(.+)$")


class Base(BaseModel):
    class Config:
        extra = "forbid"
        frozen = True
        anystr_strip_whitespace = True


class ArchitectureMap(Base):
    amd64: str | None = None
    armhf: str | None = None
    arm64: str | None = None


class MultiArchitectureFetch(Base):
    url: AnyHttpUrl = Field(..., description="URL template of the file")
    targets: ArchitectureMap | None = Field(
        None,
        description="Architecture to target name map. The URL can be templated with "
        "the target name to download the right file/archive for each architecture.",
    )


class SourceDestinationStr(str):
    source: str
    destination: str

    @classmethod
    def __get_validators__(cls) -> Any:
        yield cls.validate

    @classmethod
    def validate(cls, v: str) -> "SourceDestinationStr":
        if not isinstance(v, str):
            raise TypeError("string required")
        if len(paths := v.split(":")) != 2:
            raise TypeError("string must have one ':' separator")
        string = cls(v)
        string.source = paths[0]
        string.destination = paths[1]
        return string

    def __repr__(self) -> str:
        return (
            f"SourceDestinationStr(source={self.source}, destination={self.destination})"
        )


class HereDocument(Base):
    content: str = Field(..., description="File content")
    path: str = Field(..., description="Path where the file will be written")

    @property
    def destination(self) -> str:
        return self.path


class Matrix(Base):
    architectures: list[Architecture] = Field(
        default_factory=list, description="List of architectures"
    )
    versions: list[str] = Field(default_factory=list, description="List of versions")


class Blueprint(Base):
    name: str = Field(..., description="Package name")
    matrix: Matrix | None = Field(
        None, description="Generate multiple packages from one a single blueprint"
    )
    version: str = Field("", description="Package name")
    revision: str = Field(
        "1", regex=r"^[1-9][a-z0-9+~]*$", description="Package revision"
    )
    epoch: int = Field(0, description="Package epoch", ge=0)
    architecture: Architecture = Field("amd64", description="Package architecture")
    homepage: AnyHttpUrl | None = Field(None, description="Upstream project homepage")
    summary: str = Field(..., description="Package short description, one line only")
    description: str = Field("", description="Package description")
    build_depends: list[str] = Field(
        default_factory=list, description="Package build dependencies"
    )
    provides: list[str] = Field(
        default_factory=list,
        description="List of virtual packages provided by this package",
    )
    depends: list[str] = Field(default_factory=list, description="Package dependencies")
    recommends: list[str] = Field(
        default_factory=list, description="Package recommended dependencies"
    )
    replaces: list[str] = Field(
        default_factory=list,
        description="List of packages replaced by this package",
    )
    conflicts: list[str] = Field(
        default_factory=list,
        description="Conflicting packages, for more information read "
        "https://www.debian.org/doc/debian-policy/ch-relationships.html",
    )
    fetch: AnyHttpUrl | MultiArchitectureFetch | None = Field(
        None,
        description="Url of a file (or a file per architecture) to download before "
        "running build and install instructions",
    )
    install: list[HereDocument | SourceDestinationStr] = Field(default_factory=list)
    script: list[str] = Field(default_factory=list, description="Build instructions")

    _uid: int = PrivateAttr()

    @root_validator(pre=False)
    def _version_must_be_set(cls, values: Any) -> Any:
        matrix = values.get("matrix", None)
        if (not matrix or not matrix.versions) and not values.get("version"):
            raise ValueError("Version field is required when versions matrix is not used")
        if matrix and matrix.versions:
            values["version"] = matrix.versions[-1]
        return values

    @validator("architecture", "version", pre=True, always=False)
    def _check_arch_and_version(cls, v: Any, values: Any, field: ModelField) -> Any:
        if (matrix := values.get("matrix", None)) is not None:
            getter = getattr if isinstance(matrix, Matrix) else lambda x, y: x[y]
            if getter(matrix, f"{field.name}s"):
                raise ValueError(f"'{field.name}s' cannot be used with '{field.name}'")
        return v

    @validator("name", "version", "summary", "description", "homepage", pre=True)
    def _render_string_attributes(cls, v: Any) -> Any:
        if isinstance(v, str):
            return environment.from_string(v).render()
        return v

    def _get_additional_variables(self, architecture: str) -> dict[str, str | None]:
        target = (
            (getattr(self.fetch.targets, architecture, architecture) or architecture)
            if isinstance(self.fetch, MultiArchitectureFetch)
            else architecture
        )
        return dict(
            target=target,
            goarch=DEFAULT_GOARCH_MAP.get(architecture, None),
            rust_target=DEFAULT_RUST_TARGET_MAP.get(architecture, None),
        )

    def architectures(self) -> list[str]:
        if self.matrix and self.matrix.architectures:
            return cast(list[str], self.matrix.architectures)
        return [self.architecture]

    def versions(self) -> list[str]:
        if self.matrix and self.matrix.versions:
            return self.matrix.versions
        return [self.version]

    def render_string(self, string: str, **kwargs: str | Path | None) -> str:
        architecture: Any = kwargs.pop("architecture", None)
        architecture = architecture or self.architecture
        version: Any = kwargs.pop("version", None)
        version = version or self.version
        return environment.from_string(string).render(
            name=self.name,
            arch=architecture,
            version=version,
            **(self._get_additional_variables(architecture) | kwargs),
        )

    def render_fetch_url(
        self, version: str | None = None, architecture: str | None = None
    ) -> str | None:
        if self.fetch is None:
            return None
        url = self.fetch if isinstance(self.fetch, str) else self.fetch.url
        return self.render_string(url, version=version, architecture=architecture)

    def render_fetch_urls(self) -> list[str]:
        urls = []
        for architecture, version in product(self.architectures(), self.versions()):
            if url := self.render_fetch_url(version=version, architecture=architecture):
                urls.append(url)
        return urls

    @property
    def uid(self) -> int:
        return self._uid


@dataclass
class ConfigurationFile:
    path: Path
    lockfile_path: Path
    content: OrderedDict[str, Any] | list[OrderedDict[str, Any]]
    yaml: YAML

    @cached_property
    def raw_blueprints(self) -> list[OrderedDict[str, Any]]:
        return self.content if isinstance(self.content, list) else [self.content]

    def save(self) -> None:
        with self.path.open("w") as output:
            self.yaml.dump(self.content, output)


def get_default_lockfile_path(configuration_path: Path) -> Path:
    return configuration_path.parent / "ops2deb.lock.yml"


def load_configuration_file(configuration_path: Path) -> ConfigurationFile:
    configuration_path = configuration_path.absolute()
    yaml = YAML()
    yaml.Emitter = FixIndentEmitter

    try:
        with configuration_path.open("r") as fd:
            content = yaml.load(fd)
    except YAMLError as e:
        raise Ops2debParserError(f"Failed to parse {configuration_path}.\n{e}")
    except FileNotFoundError:
        raise Ops2debParserError(f"File not found: {configuration_path}")
    except IsADirectoryError:
        raise Ops2debParserError(f"Path points to a directory: {configuration_path}")

    # configuration file can start with "# lockfile={path_to_lockfile}"
    lockfile_path = get_default_lockfile_path(configuration_path)
    with configuration_path.open() as file:
        first_line = file.readline().strip()
        if (match := LOCKFILE_PATH_HEADER_RE.match(first_line)) is not None:
            lockfile_path = (configuration_path.parent / match.group(1)).absolute()

    return ConfigurationFile(configuration_path, lockfile_path, content, yaml)


@dataclass(frozen=True)
class BlueprintMetadata:
    configuration_file: ConfigurationFile
    index_in_configuration_file: int
    lock: Lock


@dataclass(frozen=True)
class Configuration:
    configuration_files: list[ConfigurationFile]
    lock_files: list[Lock]
    blueprints: list[Blueprint]
    metadatas: list[BlueprintMetadata]

    def get_blueprint_lock(self, blueprint: Blueprint) -> Lock:
        return self.metadatas[blueprint.uid].lock

    def get_blueprint_configuration_file(self, blueprint: Blueprint) -> ConfigurationFile:
        return self.metadatas[blueprint.uid].configuration_file

    def get_raw_blueprint(self, blueprint: Blueprint) -> OrderedDict[str, Any]:
        metadata = self.metadatas[blueprint.uid]
        configuration_file = metadata.configuration_file
        return configuration_file.raw_blueprints[metadata.index_in_configuration_file]

    def save(self) -> None:
        for configuration in self.configuration_files:
            configuration.save()
        for lock in self.lock_files:
            lock.save()


def load_configuration(search_glob: str) -> Configuration:
    # parse yaml of all configuration files matching glob pattern
    configuration_files: list[ConfigurationFile] = []
    for result in glob.iglob(search_glob, recursive=True):
        if (path := Path(result)).is_file() is False:
            continue
        if path.name.endswith(".lock.yml"):
            continue
        configuration_files.append(load_configuration_file(path))

    if not configuration_files:
        raise Ops2debParserError(
            f"Glob expression {search_glob} did not match any configuration file."
        )

    # parse all lock files, multiple config files can use the same lock file
    locks: dict[Path, Lock] = {}
    for configuration_file in configuration_files:
        lockfile_path = configuration_file.lockfile_path
        if (lock := locks.get(lockfile_path, None)) is None:
            lock = Lock(lockfile_path)
        locks[lockfile_path] = lock

    # parse blueprints from all configuration files
    blueprints: list[Blueprint] = []
    metadatas: list[BlueprintMetadata] = []
    for configuration_file in configuration_files:
        for index, raw_blueprint in enumerate(configuration_file.raw_blueprints):
            try:
                blueprint = Blueprint.parse_obj(raw_blueprint)
                blueprints.append(blueprint)
                metadata = BlueprintMetadata(
                    configuration_file, index, locks[configuration_file.lockfile_path]
                )
                metadatas.append(metadata)
            except ValidationError as e:
                raise Ops2debParserError(
                    f"Failed to parse blueprint at index {index} "
                    f"in {configuration_file.path}.\n{e}"
                )

    # assign a unique id to all ids
    for uid, blueprint in enumerate(blueprints):
        blueprint._uid = uid

    logger.title(
        f"Loaded {len(configuration_files)} configuration file(s) and "
        f"{len(blueprints)} blueprint(s)"
    )

    return Configuration(
        configuration_files=configuration_files,
        blueprints=blueprints,
        lock_files=list(locks.values()),
        metadatas=metadatas,
    )
