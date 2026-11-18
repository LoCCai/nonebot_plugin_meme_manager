"""Cold catalog browsing must not depend on every pack's manifest endpoint."""
from copy import deepcopy
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from repo.backend.packs import storage
from repo.nb2.runtime import MemeRuntime
from repo.nb2.web import get_router, ROUTER_PREFIX
from repo.nb2.web.bridge import register_routes


def test_cold_catalog_is_normal_and_fast_fetch_skips_manifest_requests(tmp_path, monkeypatch):
    runtime = MemeRuntime(data_dir=tmp_path)
    app = FastAPI()
    router = get_router()
    register_routes(router, runtime)
    app.include_router(router)
    client = TestClient(app)
    prefix = ROUTER_PREFIX + '/api/community/index'
    empty = client.get(prefix + '/cache')
    assert empty.status_code == 200
    assert empty.json()['cached'] is False
    calls, closed = [], []
    source = {'type': 'github', 'repo': 'owner/fixture', 'ref': 'main', 'subpath': '.'}
    entries = [{'id': 'fixture', 'name': 'Fixture', 'source': source}]
    monkeypatch.setattr(storage, 'validate_community_index', lambda data: data)

    def fetch(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(status_code=200, close=lambda: closed.append(url),
            json=lambda: {'id': 'fixture', 'version': '2.0'} if url.endswith('/manifest.json') else {'packs': deepcopy(entries)})

    monkeypatch.setattr(storage, '_http_get_with_optional_acceleration', fetch)
    first = client.post(prefix + '/fetch', json={'include_versions': False})
    assert first.status_code == 200
    assert first.json()['index']['packs'][0]['version_pending'] is True
    assert len(calls) == len(closed) == 1
    assert client.get(prefix + '/cache').json()['pack_count'] == 1
    # Explicit refresh preserves the existing remote version-check behavior.
    refreshed = client.post(prefix + '/fetch', json={'include_versions': True})
    assert refreshed.json()['index']['packs'][0]['version'] == '2.0'
    assert len(calls) == len(closed) == 3
    fast_again = client.post(prefix + '/fetch', json={'include_versions': False})
    assert fast_again.json()['index']['packs'][0]['version'] == '2.0'
    assert len(calls) == 4
    entries[0]['source'] = {**source, 'ref': 'other'}
    changed = client.post(prefix + '/fetch', json={'include_versions': False})
    assert 'version' not in changed.json()['index']['packs'][0]
    assert changed.json()['index']['packs'][0]['version_pending'] is True
    assert client.post(prefix + '/fetch', json={'include_versions': 'false'}).status_code == 400
