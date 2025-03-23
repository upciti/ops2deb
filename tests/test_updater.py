from typing import Optional

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from httpx import AsyncClient
from starlette.responses import JSONResponse

from ops2deb.exceptions import Ops2debUpdaterError
from ops2deb.logger import enable_debug
from ops2deb.updater import (
    BaseUpdateStrategy,
    GenericUpdateStrategy,
    GithubUpdateStrategy,
)

enable_debug(True)


@pytest.fixture
def app_factory():
    def _app_factory(versions: list[str]):
        app = FastAPI(debug=True)

        @app.head("/releases/{version}/some-app.tar.gz")
        def download_release(version: str):
            if version not in versions:
                raise HTTPException(status_code=404)

        return app

    return _app_factory


@pytest.fixture
def github_app_factory():
    def _github_app_factory(latest_release: str, versions: Optional[list[str]] = None):
        versions = versions or []
        app = FastAPI(debug=True)

        @app.head("/owner/name/releases/{version}/some-app.tar.gz")
        def github_asset(version: str):
            if version not in versions and version != latest_release:
                raise HTTPException(status_code=404)

        @app.get("/repos/owner/name/releases/latest")
        def github_release_api():
            return JSONResponse({"tag_name": latest_release})

        return app

    return _github_app_factory


async def test_try_version__returns_false_when_fetch_url_does_not_depend_on_the_blueprint_version(  # noqa: E501
    blueprint_factory,
):
    # Given
    blueprint = blueprint_factory(
        version="1.0.0",
        fetch="http://test/releases/1.0.0/some-app.tar.gz",
    )
    update_strategy = BaseUpdateStrategy(httpx.AsyncClient())

    # When
    result = await update_strategy.try_version(blueprint, "2.0.0")

    # Then
    assert result is False


@pytest.mark.parametrize(
    "versions,expected_result",
    [
        (["1.0.0", "1.1.0"], "1.1.0"),
        (["1.0.0", "1.1.3"], "1.1.3"),
        (["1.0.0", "1.0.1", "1.1.0"], "1.1.0"),
        (["1.0.0", "1.1.1", "2.0.0"], "1.1.1"),
        (["1.0.0", "2.0.0"], "2.0.0"),
        (["1.0.0", "2.0.3"], "2.0.3"),
        (["1.0.0", "1.1.0", "2.0.0"], "1.1.0"),
        (["1.0.0", "1.0.1", "1.0.2", "1.1.0", "1.1.1"], "1.1.1"),
    ],
)
async def test_generic_update_strategy_finds_latest_release_version(
    blueprint_factory, app_factory, versions, expected_result
):
    # Given
    blueprint = blueprint_factory(
        version="1.0.0",
        fetch="http://test/releases/{{version}}/some-app.tar.gz",
    )
    app = app_factory(versions)

    # When
    async with AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        update_strategy = GenericUpdateStrategy(client)
        latest_version = await update_strategy(blueprint)

    # Then
    assert latest_version == expected_result


async def test_generic_update_strategy_finds_latest_release_version_when_version_has_prerelease_part(  # noqa: E501
    blueprint_factory, app_factory
):
    # Given
    blueprint = blueprint_factory(
        version="1.0.0-pre",
        fetch="http://test/releases/{{version}}/some-app.tar.gz",
    )
    app = app_factory(["2.0.0"])

    # When
    async with AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        update_strategy = GenericUpdateStrategy(client)
        latest_version = await update_strategy(blueprint)

    # Then
    assert latest_version == "2.0.0"


@pytest.mark.parametrize(
    "fetch_url,tag_name",
    [
        ("https://github.com/owner/name/releases/{{version}}/some-app.tar.gz", "2.3.0"),
        ("https://github.com/owner/name/releases/v{{version}}/some-app.tar.gz", "v2.3.0"),
        ("https://github.com/owner/name/releases/k{{version}}/some-app.tar.gz", "k2.3.0"),
    ],
)
async def test_github_update_strategy_should_find_expected_blueprint_release(
    blueprint_factory, github_app_factory, fetch_url, tag_name
):
    app = github_app_factory(tag_name)
    blueprint = blueprint_factory(fetch=fetch_url)
    async with AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        update_strategy = GithubUpdateStrategy(client)
        assert await update_strategy(blueprint) == "2.3.0"


async def test_github_update_strategy_should_not_return_an_older_version_than_current_one(
    blueprint_factory, github_app_factory
):
    app = github_app_factory("0.1.0", versions=["1.0.0"])
    url = "https://github.com/owner/name/releases/{{version}}/some-app.tar.gz"
    blueprint = blueprint_factory(fetch=url)
    async with AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        update_strategy = GithubUpdateStrategy(client)
        assert await update_strategy(blueprint) == "1.0.0"


async def test_github_update_strategy_should_fail_gracefully_when_asset_not_found(
    blueprint_factory, github_app_factory
):
    app = github_app_factory("someapp-v2.3")
    url = "https://github.com/owner/name/releases/someapp-v{{version}}/some-app.tar.gz"
    blueprint = blueprint_factory(fetch=url)
    async with AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        with pytest.raises(Ops2debUpdaterError) as e:
            await GithubUpdateStrategy(client)(blueprint)
        assert "Failed to determine latest release URL" in str(e)
