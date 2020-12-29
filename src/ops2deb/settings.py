from pathlib import Path

from pydantic import BaseSettings


class _Settings(BaseSettings):
    verbose: bool = False
    config: Path = Path("ops2deb.yml")
    work_dir: Path = Path("output")

    class Config:
        env_prefix = "ops2deb_"


settings = _Settings()
