"""WebUI 集成测试（FastAPI TestClient，不需要真实驱动）。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from repo.nb2.runtime import MemeRuntime


@pytest.fixture()
def runtime(tmp_path):
    return MemeRuntime(data_dir=tmp_path)


@pytest.fixture()
def app(runtime):
    from repo.nb2.web import get_router

    application = FastAPI()
    application.include_router(get_router())
    return application


def test_pages_index_redirects(app):
    client = TestClient(app)
    response = client.get("/meme_manager", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/meme_manager/index.html" in response.headers["location"]


def test_bridge_js_served(app):
    client = TestClient(app)
    response = client.get("/meme_manager/bridge.js")
    assert response.status_code == 200
    assert "AstrBotPluginPage" in response.text


def test_static_page_served(app):
    client = TestClient(app)
    response = client.get("/meme_manager/app/index.html")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_static_path_traversal_blocked(app):
    client = TestClient(app)
    response = client.get("/meme_manager/app/..%2F..%2Fmain.py")
    assert response.status_code in (400, 404)


def test_api_routes_registered(runtime):
    from repo.nb2.web import get_router
    from repo.nb2.web.bridge import register_routes

    router = get_router()
    count = register_routes(router, runtime)
    paths = {route.path for route in router.routes}
    assert count > 0
    assert "/meme_manager/api/emoji" in paths
    assert "/meme_manager/api/preview/manifest" in paths
    assert "/meme_manager/api/bridge/auth_token" in paths


def test_api_emoji_endpoint_works(runtime):
    from repo.nb2.web import get_router
    from repo.nb2.web.bridge import register_routes

    application = FastAPI()
    router = get_router()
    register_routes(router, runtime)
    application.include_router(router)
    client = TestClient(application)

    response = client.get("/meme_manager/api/emoji")
    assert response.status_code == 200
    assert isinstance(response.json(), dict)


def test_api_bridge_token_endpoint(runtime):
    from repo.nb2.web import get_router
    from repo.nb2.web.bridge import register_routes

    application = FastAPI()
    router = get_router()
    register_routes(router, runtime)
    application.include_router(router)
    client = TestClient(application)

    response = client.get("/meme_manager/api/bridge/auth_token")
    assert response.status_code == 401

    response = client.get(
        "/meme_manager/api/bridge/auth_token", headers={"Authorization": "Bearer abc"}
    )
    assert response.status_code == 200
    assert response.json() == {"token": "abc"}
