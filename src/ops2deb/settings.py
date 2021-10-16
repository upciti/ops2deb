from pathlib import Path

from pydantic import BaseSettings


class _Settings(BaseSettings):
    verbose: bool = False
    config: Path = Path("ops2deb.yml")
    # directory where debian source packages are created
    work_dir: Path = Path("output")
    # directory where archives (specified with "fetch") are downloaded
    cache_dir: Path = Path("/tmp/ops2deb_cache")

    class Config:
        env_prefix = "ops2deb_"


settings = _Settings()
