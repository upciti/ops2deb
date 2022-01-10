from typing import List

import pytest
from httpx import AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response

from ops2deb.logger import enable_debug
from ops2deb.parser import Blueprint, RemoteFile
from ops2deb.updater import GenericUpdateStrategy

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
def dummy_blueprint():
    return Blueprint(
        name="some-app",
        version="1.0.0",
        summary="some summary",
        description="some description",
        fetch=RemoteFile(
            url="http://test/releases/{{version}}/some-app.tar.gz", sha256="deadbeef"
        ),
    )


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
@pytest.mark.asyncio
async def test_generic_update_strategy_should_find_expected_blueprint_release(
    dummy_blueprint, app_factory, versions, expected_result
):
    app = app_factory(versions)
    async with AsyncClient(app=app) as client:
        update_strategy = GenericUpdateStrategy(client)
        assert await update_strategy(dummy_blueprint) == expected_result
