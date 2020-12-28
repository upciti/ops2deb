from pathlib import Path

from pydantic import BaseSettings


class _Settings(BaseSettings):
    verbose: bool = False
    config: Path = Path("debops.yml")
    work_dir: Path = Path("output")

    class Config:
        env_prefix = "debops_"


settings = _Settings()
