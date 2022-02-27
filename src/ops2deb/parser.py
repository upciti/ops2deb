from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import AnyHttpUrl, BaseModel, Field, ValidationError, validator
from ruamel.yaml import YAML, YAMLError

from .exceptions import Ops2debParserError
from .jinja import DEFAULT_GOARCH_MAP, DEFAULT_RUST_TARGET_MAP, environment

Architecture = Literal["all", "amd64", "arm64", "armhf"]


class Base(BaseModel):
    class Config:
        extra = "forbid"
        allow_mutation = False
        anystr_strip_whitespace = True


class ArchitectureMap(Base):
    amd64: Optional[str] = None
    armhf: Optional[str] = None
    arm64: Optional[str] = None


class RemoteFile(Base):
    url: AnyHttpUrl = Field(..., description="URL template of the file")
    sha256: str = Field(..., description="SHA256 checksum of the file")


class MultiArchitectureRemoteFile(Base):
    url: AnyHttpUrl = Field(..., description="URL template of the file")
    sha256: ArchitectureMap = Field(..., description="SHA256 checksum of each file")
    targets: Optional[ArchitectureMap] = Field(
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


class Blueprint(Base):
    name: str = Field(..., description="Package name")
    version: str = Field(..., description="Package name")
    homepage: Optional[AnyHttpUrl] = Field(None, description="Upstream project homepage")
    revision: int = Field(1, description="Package revision")
    arch: Architecture = Field("amd64", description="Package architecture")
    summary: str = Field(..., description="Package short description, one line only")
    description: str = Field(..., description="Package description")
    depends: List[str] = Field(default_factory=list, description="Package dependencies")
    recommends: List[str] = Field(
        default_factory=list, description="Package recommended dependencies"
    )
    conflicts: List[str] = Field(
        default_factory=list,
        description="Conflicting packages, for more information read "
        "https://www.debian.org/doc/debian-policy/ch-relationships.html",
    )
    fetch: Optional[Union[RemoteFile, MultiArchitectureRemoteFile]] = Field(
        None,
        description="Describe a file (or a file per architecture) to download before "
        "running the build script",
    )
    install: List[Union[HereDocument, SourceDestinationStr]] = Field(default_factory=list)
    script: List[str] = Field(default_factory=list, description="Build instructions")

    @validator("*", pre=True)
    def _render_string_attributes(cls, v: Any) -> str:
        if isinstance(v, str):
            return environment.from_string(v).render()
        return v

    def _get_additional_variables(self) -> Dict[str, Optional[str]]:
        target = (
            (getattr(self.fetch.targets, self.arch, self.arch) or self.arch)
            if isinstance(self.fetch, MultiArchitectureRemoteFile)
            else self.arch
        )
        return dict(
            target=target,
            goarch=DEFAULT_GOARCH_MAP.get(self.arch, None),
            rust_target=DEFAULT_RUST_TARGET_MAP.get(self.arch, None),
        )

    def supported_architectures(self) -> List[str]:
        if isinstance(self.fetch, MultiArchitectureRemoteFile):
            return list(self.fetch.sha256.dict(exclude_none=True).keys())
        else:
            return [self.arch]

    def render_string(self, string: str, **kwargs: Optional[str | Path]) -> str:
        version = kwargs.pop("version", None)
        version = version or self.version
        return environment.from_string(string).render(
            name=self.name,
            arch=self.arch,
            version=version,
            **(self._get_additional_variables() | kwargs),
        )

    def render_fetch(self, version: Optional[str] = None) -> Optional[RemoteFile]:
        if self.fetch is None:
            return None
        if isinstance(self.fetch, MultiArchitectureRemoteFile):
            sha256 = getattr(self.fetch.sha256, self.arch)
        else:
            sha256 = self.fetch.sha256
        if sha256 is None:
            return None
        url = self.render_string(
            self.fetch.url,
            version=version,
            sha256=sha256,
        )
        return RemoteFile(url=url, sha256=sha256)

    def render_script(self, src: Path, debian: Path) -> List[str]:
        return [self.render_string(line, src=src, debian=debian) for line in self.script]


class Configuration(Base):
    __root__: Union[List[Blueprint], Blueprint]


def extend(blueprints: List[Blueprint]) -> List[Blueprint]:
    extended_list: List[Blueprint] = []
    for blueprint in blueprints:
        for arch in blueprint.supported_architectures():
            extended_list.append(blueprint.copy(update={"arch": arch}))
    return extended_list


def load(
    configuration_path: Path, yaml: YAML = YAML()
) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
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
    configuration_dict: Union[List[Dict[str, Any]], Dict[str, Any]]
) -> List[Blueprint]:
    try:
        blueprints = Configuration.parse_obj(configuration_dict).__root__
    except ValidationError as e:
        raise Ops2debParserError(f"Invalid configuration file.\n{e}")
    if isinstance(blueprints, Blueprint):
        blueprints = [blueprints]
    return blueprints


def parse(configuration_path: Path) -> List[Blueprint]:
    return validate(load(configuration_path))
