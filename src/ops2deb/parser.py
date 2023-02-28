import re
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

from ops2deb.exceptions import Ops2debParserError
from ops2deb.jinja import DEFAULT_GOARCH_MAP, DEFAULT_RUST_TARGET_MAP, environment
from ops2deb.utils import FixIndentEmitter

Architecture = Literal["all", "amd64", "arm64", "armhf"]


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

    _index: int = PrivateAttr()

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
    def index(self) -> int:
        return self._index


class _ConfigurationFile(Base):
    __root__: list[Blueprint] | Blueprint


class Configuration:
    def __init__(self, configuration_path: Path, yaml: YAML | None = None) -> None:
        self.yaml: YAML = yaml or YAML()
        self.yaml.Emitter = FixIndentEmitter
        self.path = configuration_path
        self._blueprints_dict = self._parse_yaml()
        self._blueprints = self._parse_blueprints()
        self.lockfile_path = self._parse_lockfile_path()

    @property
    def blueprints(self) -> list[Blueprint]:
        return self._blueprints

    @cached_property
    def raw_blueprints(self) -> list[OrderedDict[str, Any]]:
        return (
            self._blueprints_dict
            if isinstance(self._blueprints_dict, list)
            else [self._blueprints_dict]
        )

    def save(self) -> None:
        with self.path.open("w") as output:
            self.yaml.dump(self._blueprints_dict, output)

    def _parse_yaml(self) -> Any:
        try:
            return self.yaml.load(self.path.open("r"))
        except YAMLError as e:
            raise Ops2debParserError(f"Invalid YAML file.\n{e}")
        except FileNotFoundError:
            raise Ops2debParserError(f"File not found: {self.path.absolute()}")
        except IsADirectoryError:
            raise Ops2debParserError(
                f"Path points to a directory: {self.path.absolute()}"
            )

    def _parse_blueprints(self) -> list[Blueprint]:
        try:
            blueprints = _ConfigurationFile.parse_obj(self._blueprints_dict).__root__
        except ValidationError as e:
            raise Ops2debParserError(f"Invalid configuration file.\n{e}")
        if isinstance(blueprints, Blueprint):
            blueprints = [blueprints]
        for index, blueprint in enumerate(blueprints):
            blueprint._index = index
        return blueprints

    def _parse_lockfile_path(self) -> Path | None:
        lockfile_path_re = re.compile(r"^# lockfile=(.+)$")
        with self.path.open() as file:
            first_line = file.readline().strip()
        if (match := lockfile_path_re.match(first_line)) is not None:
            return Path(match.group(1))
        return None
