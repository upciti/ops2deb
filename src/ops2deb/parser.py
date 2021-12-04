from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from jinja2 import Environment, PackageLoader
from pydantic import AnyHttpUrl, BaseModel, Field, ValidationError
from ruamel.yaml import YAML, YAMLError

from .exceptions import ParseError

environment = Environment(loader=PackageLoader("ops2deb", "templates"))
Architecture = Literal["all", "amd64", "armhf"]


class Base(BaseModel):
    class Config:
        extra = "forbid"
        allow_mutation = False


class RemoteFile(Base):
    url: AnyHttpUrl = Field(..., description="File URL")
    sha256: str = Field(..., description="File SHA256 checksum")


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
    fetch: Optional[RemoteFile] = Field(None, description="File to download")
    script: List[str] = Field(default_factory=list, description="Build instructions")

    class Config:
        anystr_strip_whitespace = True

    @property
    def debian_version(self) -> str:
        return f"{self.version}-{self.revision}~ops2deb"

    def _render_str(self, string: str, **kwargs: Optional[str]) -> str:
        version = kwargs.pop("version", None)
        version = version or self.version
        return environment.from_string(string).render(
            name=self.name,
            arch=self.arch,
            version=version,
            **kwargs,
        )

    def render(
        self, src: Optional[Path] = None, version: Optional[str] = None
    ) -> "Blueprint":
        update: Dict[str, Any] = {}

        if src is not None:
            update["script"] = [
                self._render_str(line, src=str(src)) for line in self.script
            ]

        if self.fetch is not None:
            update["fetch"] = RemoteFile(
                url=self._render_str(self.fetch.url, version=version),
                sha256=self.fetch.sha256,
            )

        return self.copy(update=update)


class Configuration(Base):
    __root__: List[Blueprint]


def load(configuration_path: Path, yaml: YAML) -> List[Dict[str, Any]]:
    try:
        return yaml.load(configuration_path.open("r"))
    except YAMLError as e:
        raise ParseError(f"Invalid YAML file.\n{e}")
    except FileNotFoundError:
        raise ParseError(f"File not found: {configuration_path.absolute()}")


def validate(configuration_dict: List[Dict[str, Any]]) -> Configuration:
    try:
        return Configuration.parse_obj(configuration_dict)
    except ValidationError as e:
        raise ParseError(f"Invalid configuration file.\n{e}")


def parse(configuration_path: Path) -> Configuration:
    yaml = YAML(typ="safe")
    return validate(load(configuration_path, yaml))
