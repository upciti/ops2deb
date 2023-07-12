import glob
import re
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any, Literal, OrderedDict, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    GetCoreSchemaHandler,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import CoreSchema, core_schema
from ruamel.yaml import YAML, YAMLError  # type: ignore[attr-defined]

from ops2deb.exceptions import Ops2debParserError
from ops2deb.jinja import DEFAULT_GOARCH_MAP, DEFAULT_RUST_TARGET_MAP, environment
from ops2deb.lockfile import LockFile
from ops2deb.utils import FixIndentEmitter

Architecture = Literal["all", "amd64", "arm64", "armhf"]

LOCKFILE_PATH_HEADER_RE = re.compile(r"^# lockfile=(.+)$")


class Base(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class ArchitectureMap(Base):
    amd64: str | None = None
    armhf: str | None = None
    arm64: str | None = None


class MultiArchitectureFetch(Base):
    url: str = Field(..., description="URL template of the file")
    targets: ArchitectureMap | None = Field(
        None,
        description="Architecture to target name map. The URL can be templated with "
        "the target name to download the right file/archive for each architecture.",
    )


class SourceDestinationStr(str):
    source: str
    destination: str

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        def validate(v: str) -> "SourceDestinationStr":
            if not isinstance(v, str):
                raise ValueError("string required")
            if len(paths := v.split(":")) != 2:
                raise ValueError("string must have one ':' separator")
            string = cls(v)
            string.source = paths[0]
            string.destination = paths[1]
            return string

        return core_schema.no_info_plain_validator_function(validate)

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
        "1", pattern=r"^[1-9][a-z0-9+~]*$", description="Package revision"
    )
    epoch: int = Field(0, description="Package epoch", ge=0)
    architecture: Architecture = Field("amd64", description="Package architecture")
    homepage: str | None = Field(None, description="Upstream project homepage")
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
    fetch: str | MultiArchitectureFetch | None = Field(
        None,
        description="Url of a file (or a file per architecture) to download before "
        "running build and install instructions",
    )
    install: list[HereDocument | SourceDestinationStr] = Field(default_factory=list)
    script: list[str] = Field(default_factory=list, description="Build instructions")

    _uid: int = PrivateAttr()
    _index_in_configuration: int = PrivateAttr()

    @model_validator(mode="after")
    def _version_and_architecture_cant_be_used_with_matrix(self) -> "Blueprint":
        matrix = self.matrix
        for field in "version", "architecture":
            if field in self.model_fields_set and matrix is not None:
                if f"{field}s" in matrix.model_fields_set:
                    raise ValueError(f"'{field}s' cannot be used with '{field}'")
        if (not matrix or not matrix.versions) and "version" not in self.model_fields_set:
            raise ValueError("Version field is required when versions matrix is not used")
        return self

    @field_validator("revision", mode="before")
    def _convert_revision_to_str(cls, value: Any) -> Any:
        if isinstance(value, int):
            return str(value)
        return value

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

    @classmethod
    def build(cls, obj: Any) -> "Blueprint":
        blueprint = cls.model_validate(obj)
        if blueprint.matrix and (versions := blueprint.matrix.versions):
            blueprint.version = versions[-1]
        for string in "name", "version", "summary", "description", "homepage":
            value = getattr(blueprint, string, None)
            if isinstance(value, str):
                setattr(blueprint, string, environment.from_string(value).render())
        return blueprint

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

    def render_fetch_urls(
        self, version: str | None = None, architecture: str | None = None
    ) -> list[str]:
        urls = []
        architectures = [architecture] if architecture else self.architectures()
        versions = [version] if version else self.versions()
        for architecture, version in product(architectures, versions):
            if url := self.render_fetch_url(version=version, architecture=architecture):
                urls.append(url)
        return urls

    @property
    def uid(self) -> int:
        return self._uid

    @property
    def index(self) -> int:
        return self._index_in_configuration


@dataclass
class ConfigurationFile:
    path: Path
    lockfile_path: Path
    content: OrderedDict[str, Any] | list[OrderedDict[str, Any]]
    yaml: YAML
    raw_blueprints: list[OrderedDict[str, Any]]
    blueprints: list[Blueprint]
    tainted: bool = False


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

    raw_blueprints = content if isinstance(content, list) else [content]

    blueprints: list[Blueprint] = []
    for index, raw_blueprint in enumerate(raw_blueprints):
        try:
            blueprint = Blueprint.build(raw_blueprint)
            blueprint._index_in_configuration = index
            blueprints.append(blueprint)
        except ValidationError as e:
            raise Ops2debParserError(
                f"Failed to parse blueprint at index {index} "
                f"in {configuration_path}.\n{e}"
            )

    return ConfigurationFile(
        path=configuration_path,
        lockfile_path=lockfile_path,
        content=content,
        yaml=yaml,
        raw_blueprints=raw_blueprints,
        blueprints=blueprints,
    )


def save_configuration_file(configuration_file: ConfigurationFile) -> None:
    if configuration_file.tainted is False:
        return
    with configuration_file.path.open("w") as output:
        configuration_file.yaml.dump(configuration_file.content, output)


def load_configuration_files(
    configurations_search_pattern: str,
) -> list[ConfigurationFile]:
    configuration_files: list[ConfigurationFile] = []
    for result in glob.iglob(configurations_search_pattern, recursive=True):
        if (path := Path(result)).is_file() is False:
            continue
        if path.name.endswith(".lock.yml"):
            continue
        configuration_files.append(load_configuration_file(path))

    if not configuration_files:
        raise Ops2debParserError(
            f"Glob expression {configurations_search_pattern} "
            f"did not match any configuration file."
        )

    return configuration_files


@dataclass(frozen=True)
class BlueprintMetadata:
    configuration_file: ConfigurationFile
    lock: LockFile


@dataclass(frozen=True)
class Resources:
    configuration_files: list[ConfigurationFile]
    lock_files: list[LockFile]
    blueprints: list[Blueprint]
    metadatas: list[BlueprintMetadata]

    def get_blueprint_lock(self, blueprint: Blueprint) -> LockFile:
        return self.metadatas[blueprint.uid].lock

    def get_blueprint_configuration_file(self, blueprint: Blueprint) -> ConfigurationFile:
        return self.metadatas[blueprint.uid].configuration_file

    def get_raw_blueprint(self, blueprint: Blueprint) -> OrderedDict[str, Any]:
        metadata = self.metadatas[blueprint.uid]
        configuration_file = metadata.configuration_file
        configuration_file.tainted = True
        return configuration_file.raw_blueprints[blueprint.index]

    def save(self) -> None:
        for configuration in self.configuration_files:
            save_configuration_file(configuration)
        for lock in self.lock_files:
            lock.save()


def load_resources(configurations_search_pattern: str) -> Resources:
    configuration_files = load_configuration_files(configurations_search_pattern)

    # parse all lock files, multiple config files can use the same lock file
    locks: dict[Path, LockFile] = {}
    for configuration_file in configuration_files:
        lockfile_path = configuration_file.lockfile_path
        if (lock := locks.get(lockfile_path, None)) is None:
            lock = LockFile(lockfile_path)
        locks[lockfile_path] = lock

    # list all blueprints, and their metadata (associated lockfile and config file)
    blueprints: list[Blueprint] = []
    metadatas: list[BlueprintMetadata] = []
    for configuration_file in configuration_files:
        for index, blueprint in enumerate(configuration_file.blueprints):
            blueprints.append(blueprint)
            metadata = BlueprintMetadata(
                configuration_file, locks[configuration_file.lockfile_path]
            )
            metadatas.append(metadata)

    # assign a unique id to all ids
    for uid, blueprint in enumerate(blueprints):
        blueprint._uid = uid

    return Resources(
        configuration_files=configuration_files,
        lock_files=list(locks.values()),
        blueprints=blueprints,
        metadatas=metadatas,
    )
