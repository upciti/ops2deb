from typing import Any

import httpx


def client_factory(**kwargs: Any) -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(retries=1)
    return httpx.AsyncClient(transport=transport, follow_redirects=True, **kwargs)
