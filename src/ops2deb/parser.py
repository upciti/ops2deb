from pathlib import Path
from typing import Any, Literal, Sequence

from pydantic import AnyHttpUrl, BaseModel, Field, ValidationError, validator
from ruamel.yaml import YAML, YAMLError  # type: ignore[attr-defined]

from ops2deb.exceptions import Ops2debParserError
from ops2deb.jinja import DEFAULT_GOARCH_MAP, DEFAULT_RUST_TARGET_MAP, environment

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


class RemoteFile(Base):
    url: AnyHttpUrl = Field(..., description="URL template of the file")
    sha256: str = Field(..., description="SHA256 checksum of the file (deprecated)")


class MultiArchitectureFetch(Base):
    url: AnyHttpUrl = Field(..., description="URL template of the file")
    sha256: ArchitectureMap | None = Field(
        None, description="SHA256 checksum of each file (deprecated)"
    )
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


class Blueprint(Base):
    name: str = Field(..., description="Package name")
    matrix: Matrix | None = Field(
        None, description="Generate multiple packages from one a single blueprint"
    )
    version: str = Field(..., description="Package name")
    revision: int = Field(1, description="Package revision", ge=1)
    epoch: int = Field(0, description="Package epoch", ge=0)
    architecture: Architecture = Field("amd64", description="Package architecture")
    arch: Architecture = Field("amd64", description="Package architecture (deprecated)")
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
    fetch: AnyHttpUrl | MultiArchitectureFetch | RemoteFile | None = Field(
        None,
        description="Url of a file (or a file per architecture) to download before "
        "running build and install instructions",
    )
    install: list[HereDocument | SourceDestinationStr] = Field(default_factory=list)
    script: list[str] = Field(default_factory=list, description="Build instructions")

    @validator("arch", "architecture", pre=False, always=False)
    def _archs_cannot_be_used_with_arch(cls, v: Any, values: Any) -> Any:
        if values["matrix"] and values["matrix"]["architectures"]:
            raise ValueError("'architectures' cannot be used with 'architecture'")
        values["architecture"] = v
        return v

    @validator("name", "version", "summary", "description", "homepage", pre=True)
    def _render_string_attributes(cls, v: Any) -> Any:
        if isinstance(v, str):
            return environment.from_string(v).render()
        return v

    def _get_additional_variables(self) -> dict[str, str | None]:
        target = (
            (
                getattr(self.fetch.targets, self.architecture, self.architecture)
                or self.architecture
            )
            if isinstance(self.fetch, MultiArchitectureFetch)
            else self.architecture
        )
        return dict(
            target=target,
            goarch=DEFAULT_GOARCH_MAP.get(self.architecture, None),
            rust_target=DEFAULT_RUST_TARGET_MAP.get(self.architecture, None),
        )

    def architectures(self) -> Sequence[str]:
        if self.matrix and self.matrix.architectures:
            return self.matrix.architectures
        elif isinstance(self.fetch, MultiArchitectureFetch):
            if self.fetch.sha256 is not None:
                return list(self.fetch.sha256.dict(exclude_none=True).keys())
        return [self.architecture]

    def render_string(self, string: str, **kwargs: str | Path | None) -> str:
        version = kwargs.pop("version", None)
        version = version or self.version
        return environment.from_string(string).render(
            name=self.name,
            arch=self.architecture,
            version=version,
            **(self._get_additional_variables() | kwargs),
        )

    def render_fetch_url(self, version: str | None = None) -> str | None:
        if self.fetch is None:
            return None
        url = self.fetch if isinstance(self.fetch, str) else self.fetch.url
        return self.render_string(url, version=version)

    def render_fetch(self, version: str | None = None) -> str | RemoteFile | None:
        url = self.render_fetch_url(version=version)
        sha256 = None
        if isinstance(self.fetch, MultiArchitectureFetch):
            if self.fetch.sha256 is not None:
                sha256 = getattr(self.fetch.sha256, self.architecture, None)
        elif isinstance(self.fetch, RemoteFile):
            sha256 = self.fetch.sha256
        if sha256 and url:
            return RemoteFile(url=self.render_fetch_url(version), sha256=sha256)
        return url


class Configuration(Base):
    __root__: list[Blueprint] | Blueprint


def extend(blueprints: list[Blueprint]) -> list[Blueprint]:
    extended_list: list[Blueprint] = []
    for blueprint in blueprints:
        for arch in blueprint.architectures():
            extended_list.append(blueprint.copy(update={"architecture": arch}))
    return extended_list


def load(configuration_path: Path, yaml: YAML = YAML()) -> Any:
    try:
        return yaml.load(configuration_path.open("r"))
    except YAMLError as e:
        raise Ops2debParserError(f"Invalid YAML file.\n{e}")
    except FileNotFoundError:
        raise Ops2debParserError(f"File not found: {configuration_path.absolute()}")
    except IsADirectoryError:
        raise Ops2debParserError(
            f"Path points to a directory: {configuration_path.absolute()}"
        )


def validate(
    configuration_dict: list[dict[str, Any]] | dict[str, Any]
) -> list[Blueprint]:
    try:
        blueprints = Configuration.parse_obj(configuration_dict).__root__
    except ValidationError as e:
        raise Ops2debParserError(f"Invalid configuration file.\n{e}")
    if isinstance(blueprints, Blueprint):
        blueprints = [blueprints]
    return blueprints


def parse(configuration_path: Path) -> list[Blueprint]:
    return validate(load(configuration_path))
