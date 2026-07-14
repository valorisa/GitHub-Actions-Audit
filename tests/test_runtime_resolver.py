"""Tests de runtime_resolver.py — GO_VERSION et NODE_VERSION du workflow d'exemple."""

import httpx

from gha_audit.models import RuntimeVar, YamlPath
from gha_audit.resolver.cache import DiskCache
from gha_audit.resolver.runtime_resolver import (
    RuntimeClient,
    resolve_all_runtimes,
    resolve_go_version,
    resolve_node_version,
    resolve_runtime_version,
)


def make_client(tmp_path, handler) -> RuntimeClient:
    cache = DiskCache(tmp_path / "cache.json", ttl_seconds=3600)
    return RuntimeClient(cache=cache, transport=httpx.MockTransport(handler))


def _runtime_var(name: str, value: str) -> RuntimeVar:
    return RuntimeVar(file_path="ci.yml", name=name, value=value, yaml_path=YamlPath(("env", name)))


def test_resolve_go_version_ignores_unstable_and_picks_max(tmp_path):
    """Robustesse à l'ordre : la version stable la plus haute n'est pas
    forcément en tête de la réponse go.dev/dl."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "go.dev/dl" in str(request.url)
        return httpx.Response(
            200,
            json=[
                {"version": "go1.23rc1", "stable": False},
                {"version": "go1.21.13", "stable": True},
                {"version": "go1.22.5", "stable": True},
            ],
        )

    client = make_client(tmp_path, handler)
    resolved = resolve_go_version(client)

    assert resolved.latest == "1.22.5"
    assert resolved.source == "go_dl"
    assert resolved.confidence == "high"


def test_resolve_go_version_on_real_example_shows_outdated_gap(tmp_path):
    """GO_VERSION: '1.21' du workflow — la résolution doit renvoyer une
    version plus récente que celle déclarée, pour matérialiser l'écart."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"version": "go1.22.5", "stable": True}])

    client = make_client(tmp_path, handler)
    resolved = resolve_go_version(client)
    assert resolved.latest == "1.22.5"


def test_resolve_go_version_network_error_is_unresolvable(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    client = make_client(tmp_path, handler)
    resolved = resolve_go_version(client)

    assert resolved.latest is None
    assert resolved.source == "unresolvable"


def test_resolve_node_version_lts_track_ignores_current(tmp_path):
    """NODE_VERSION: '18' — le suivi 'lts' doit ignorer une version Current
    plus haute mais pas LTS (ex: v22 Current pendant que v20 est la LTS active)."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "nodejs.org/dist" in str(request.url)
        return httpx.Response(
            200,
            json=[
                {"version": "v22.1.0", "lts": False},
                {"version": "v20.11.0", "lts": "Iron"},
                {"version": "v18.20.0", "lts": "Hydrogen"},
            ],
        )

    client = make_client(tmp_path, handler)
    resolved = resolve_node_version(client, track="lts")

    assert resolved.latest == "20.11.0"
    assert resolved.source == "node_dist"
    assert "Iron" in resolved.note


def test_resolve_node_version_robust_to_unsorted_api_response(tmp_path):
    """Ne suppose jamais que index.json est trié — calcule le max explicitement."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"version": "v18.20.0", "lts": "Hydrogen"},
                {"version": "v20.11.0", "lts": "Iron"},  # plus récent, mais pas en tête
            ],
        )

    client = make_client(tmp_path, handler)
    resolved = resolve_node_version(client, track="lts")
    assert resolved.latest == "20.11.0"


def test_resolve_node_version_current_track(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"version": "v22.1.0", "lts": False},
                {"version": "v20.11.0", "lts": "Iron"},
            ],
        )

    client = make_client(tmp_path, handler)
    resolved = resolve_node_version(client, track="current")
    assert resolved.latest == "22.1.0"


def test_resolve_node_version_no_lts_available_is_unresolvable(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"version": "v23.0.0", "lts": False}])

    client = make_client(tmp_path, handler)
    resolved = resolve_node_version(client, track="lts")
    assert resolved.latest is None
    assert resolved.source == "unresolvable"


def test_resolve_runtime_version_dispatches_go(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"version": "go1.22.5", "stable": True}])

    client = make_client(tmp_path, handler)
    resolved = resolve_runtime_version(client, _runtime_var("GO_VERSION", "1.21"))
    assert resolved.latest == "1.22.5"


def test_resolve_runtime_version_dispatches_node_with_lts_policy(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"version": "v20.11.0", "lts": "Iron"}])

    client = make_client(tmp_path, handler)
    resolved = resolve_runtime_version(client, _runtime_var("NODE_VERSION", "18"))
    assert resolved.latest == "20.11.0"


def test_resolve_runtime_version_unsupported_name_is_unresolvable_not_error(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Ne devrait jamais appeler l'API pour un runtime non supporté")

    client = make_client(tmp_path, handler)
    resolved = resolve_runtime_version(client, _runtime_var("PYTHON_VERSION", "3.12"))
    assert resolved.source == "unresolvable"
    assert "PYTHON_VERSION" in resolved.note


def test_resolve_all_runtimes_on_real_workflow_example(tmp_path):
    """GO_VERSION + NODE_VERSION du workflow d'exemple, résolus en une passe."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "go.dev" in url:
            return httpx.Response(200, json=[{"version": "go1.22.5", "stable": True}])
        if "nodejs.org" in url:
            return httpx.Response(200, json=[{"version": "v20.11.0", "lts": "Iron"}])
        raise AssertionError(f"Unexpected call: {url}")

    runtimes = [_runtime_var("GO_VERSION", "1.21"), _runtime_var("NODE_VERSION", "18")]
    client = make_client(tmp_path, handler)
    resolved = resolve_all_runtimes(client, runtimes)

    assert resolved["GO_VERSION"].latest == "1.22.5"
    assert resolved["NODE_VERSION"].latest == "20.11.0"


def test_resolve_all_runtimes_deduplicates_by_name(tmp_path):
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json=[{"version": "go1.22.5", "stable": True}])

    runtimes = [_runtime_var("GO_VERSION", "1.21"), _runtime_var("GO_VERSION", "1.21")]
    client = make_client(tmp_path, handler)
    resolve_all_runtimes(client, runtimes)
    assert calls["count"] == 1
