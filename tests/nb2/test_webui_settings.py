"""WebUI 设置页与插件管理桩的回归测试。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from repo.nb2.runtime import MemeRuntime
from repo.nb2.web import ROUTER_PREFIX, get_router
from repo.nb2.web.bridge import register_routes


@pytest.fixture()
def client(tmp_path):
    runtime = MemeRuntime(data_dir=tmp_path)
    app = FastAPI()
    app.state.meme_runtime = runtime
    router = get_router()
    register_routes(router, runtime)
    app.include_router(router)
    return TestClient(app)


def test_settings_snapshot_has_revision_and_fields(client):
    response = client.get(f"{ROUTER_PREFIX}/api/settings/config")
    assert response.status_code == 200
    body = response.json()
    assert body.get("revision")
    assert isinstance(body.get("fields"), list) and body["fields"]
    assert "providers" in body


def test_settings_save_applies_change(client):
    snapshot = client.get(f"{ROUTER_PREFIX}/api/settings/config").json()
    response = client.post(
        f"{ROUTER_PREFIX}/api/settings/config",
        json={
            "revision": snapshot["revision"],
            "changes": {"semantic.top_k": 7},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json().get("applied") is True

    fields = client.get(f"{ROUTER_PREFIX}/api/settings/config").json()["fields"]
    current = next(item for item in fields if item["path"] == "semantic.top_k")
    assert current["value"] == 7
    assert client.app.state.meme_runtime.semantic_top_k == 7


def test_host_providers_survive_settings_and_are_not_exposed(client):
    runtime = client.app.state.meme_runtime
    runtime.config['providers'] = [{'id':'fixture','api_key':'private-test-value'}]
    runtime.config.save()
    snapshot = client.get(f"{ROUTER_PREFIX}/api/settings/config").json()
    response = client.post(f"{ROUTER_PREFIX}/api/settings/config", json={
        'revision':snapshot['revision'],'changes':{'semantic.top_k':8}})
    assert response.json()['applied'] is True
    assert 'private-test-value' not in response.text
    assert runtime.config['providers'][0]['api_key'] == 'private-test-value'
    assert runtime.auto_collect_manager.plugin is runtime


def test_busy_manual_collection_rejects_before_writing(client):
    runtime = client.app.state.meme_runtime
    snapshot = client.get(f"{ROUTER_PREFIX}/api/settings/config").json()
    original = runtime.config.path.read_bytes()
    runtime.manual_collect_manager.sessions['test'] = object()
    response = client.post(f"{ROUTER_PREFIX}/api/settings/config", json={
        'revision':snapshot['revision'],'changes':{'semantic.top_k':8}})
    assert response.status_code == 409
    assert runtime.config.path.read_bytes() == original
    runtime.manual_collect_manager.sessions.clear()


def test_failed_write_never_reports_applied(client, monkeypatch):
    runtime = client.app.state.meme_runtime
    snapshot = client.get(f"{ROUTER_PREFIX}/api/settings/config").json()
    monkeypatch.setattr(runtime.config, 'save_config', lambda values: False)
    response = client.post(f"{ROUTER_PREFIX}/api/settings/config", json={
        'revision':snapshot['revision'],'changes':{'semantic.top_k':8}})
    assert response.status_code == 500
    assert runtime.semantic_top_k == 5


def test_settings_save_rejects_stale_revision(client):
    response = client.post(
        f"{ROUTER_PREFIX}/api/settings/config",
        json={"revision": "stale", "changes": {"semantic.top_k": 9}},
    )
    assert response.status_code == 409


def test_settings_save_rejects_unknown_field(client):
    snapshot = client.get(f"{ROUTER_PREFIX}/api/settings/config").json()
    response = client.post(
        f"{ROUTER_PREFIX}/api/settings/config",
        json={"revision": snapshot["revision"], "changes": {"no.such.field": 1}},
    )
    assert response.status_code == 400


def test_local_pack_create_and_uninstall(client):
    response = client.post(
        f"{ROUTER_PREFIX}/api/packs/create",
        json={"name": "测试包", "description": "d", "pack_id": "nb2-test-pack"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["pack_id"] == "nb2-test-pack"

    packs = client.get(f"{ROUTER_PREFIX}/api/packs").json()["packs"]
    assert "nb2-test-pack" in {str(item["id"]) for item in packs}

    detail = client.get(f"{ROUTER_PREFIX}/api/packs/nb2-test-pack")
    assert detail.status_code == 200

    response = client.post(
        f"{ROUTER_PREFIX}/api/packs/uninstall", json={"pack_id": "nb2-test-pack"}
    )
    assert response.status_code < 500
