from typing import List

import pytest
from httpx import AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ops2deb.exceptions import Ops2debUpdaterError
from ops2deb.logger import enable_debug
from ops2deb.parser import Blueprint, RemoteFile
from ops2deb.updater import GenericUpdateStrategy, GithubUpdateStrategy

enable_debug(True)


@pytest.fixture
def app_factory():
    def _app_response(request: Request):
        return Response(status_code=200)

    def _app_factory(versions: List[str]):
        app = Starlette(debug=True)
        for version in versions:
            app.add_route(
                f"/releases/{version}/some-app.tar.gz", _app_response, ["HEAD", "GET"]
            )
        return app

    return _app_factory


@pytest.fixture
def github_app_factory():
    def _github_app_factory(tag_name: str):
        app = Starlette(debug=True)

        @app.route(f"/owner/name/releases/{tag_name}/some-app.tar.gz")
        def github_asset(request: Request):
            return Response()

        @app.route("/repos/owner/name/releases/latest")
        def github_release_api(request: Request):
            return JSONResponse({"tag_name": tag_name})

        return app

    return _github_app_factory


@pytest.fixture
def blueprint_factory():
    def _blueprint_factory(fetch_url: str):
        return Blueprint(
            name="some-app",
            version="1.0.0",
            summary="some summary",
            description="some description",
            fetch=RemoteFile(url=fetch_url, sha256="deadbeef"),
        )

    return _blueprint_factory


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
async def test_generic_update_strategy_should_find_expected_blueprint_release(
    blueprint_factory, app_factory, versions, expected_result
):
    fetch_url = "http://test/releases/{{version}}/some-app.tar.gz"
    app = app_factory(versions)
    async with AsyncClient(app=app) as client:
        update_strategy = GenericUpdateStrategy(client)
        assert await update_strategy(blueprint_factory(fetch_url)) == expected_result


@pytest.mark.parametrize(
    "fetch_url,tag_name",
    [
        ("https://github.com/owner/name/releases/{{version}}/some-app.tar.gz", "2.3.0"),
        ("https://github.com/owner/name/releases/v{{version}}/some-app.tar.gz", "v2.3.0"),
    ],
)
async def test_github_update_strategy_should_find_expected_blueprint_release(
    blueprint_factory, github_app_factory, fetch_url, tag_name
):
    app = github_app_factory(tag_name)
    async with AsyncClient(app=app) as client:
        update_strategy = GithubUpdateStrategy(client)
        assert await update_strategy(blueprint_factory(fetch_url)) == "2.3.0"


async def test_github_update_strategy_should_fail_gracefully_when_asset_not_found(
    blueprint_factory, github_app_factory
):
    app = github_app_factory(tag_name="someapp-v2.3.0")
    url = "https://github.com/owner/name/releases/someapp-v{{version}}/some-app.tar.gz"
    async with AsyncClient(app=app) as client:
        with pytest.raises(Ops2debUpdaterError) as e:
            await GithubUpdateStrategy(client)(blueprint_factory(fetch_url=url))
        assert "Failed to determine latest release URL" in str(e)
