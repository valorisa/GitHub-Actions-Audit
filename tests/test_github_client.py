"""Tests du client GitHub — httpx.MockTransport, aucun appel réseau réel."""

import httpx
import pytest

from gha_audit.resolver.cache import DiskCache
from gha_audit.resolver.github_client import (
    GitHubClient,
    RateLimitExceeded,
    resolve_github_token,
)


def make_client(tmp_path, handler) -> GitHubClient:
    cache = DiskCache(tmp_path / "cache.json", ttl_seconds=3600)
    transport = httpx.MockTransport(handler)
    return GitHubClient(cache=cache, transport=transport)


def test_get_latest_release_success(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/actions/checkout/releases/latest"
        return httpx.Response(200, json={"tag_name": "v5.0.0"})

    client = make_client(tmp_path, handler)
    result = client.get_latest_release("actions", "checkout")
    assert result["tag_name"] == "v5.0.0"


def test_get_latest_release_404_returns_none(tmp_path):
    """zaproxy/action-baseline ne publie pas de Release GitHub formelle —
    404 est un résultat valide, pas une erreur à faire remonter."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    client = make_client(tmp_path, handler)
    result = client.get_latest_release("zaproxy", "action-baseline")
    assert result is None


def test_get_tags_fallback(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/zaproxy/action-baseline/tags"
        return httpx.Response(200, json=[{"name": "v0.10.0"}, {"name": "v0.9.0"}])

    client = make_client(tmp_path, handler)
    tags = client.get_tags("zaproxy", "action-baseline")
    assert tags[0]["name"] == "v0.10.0"


def test_get_tags_empty_returns_empty_list_not_none(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = make_client(tmp_path, handler)
    tags = client.get_tags("someorg", "no-tags-repo")
    assert tags == []


def test_rate_limit_raises_dedicated_exception(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"X-RateLimit-Remaining": "0"}, json={"message": "rate limited"})

    client = make_client(tmp_path, handler)
    with pytest.raises(RateLimitExceeded):
        client.get_latest_release("actions", "checkout")


def test_forbidden_without_rate_limit_header_raises_http_error(tmp_path):
    """Un 403 qui n'est PAS un rate limit (ex: repo privé) doit remonter
    une erreur générique, pas être confondu avec RateLimitExceeded."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Forbidden"})

    client = make_client(tmp_path, handler)
    with pytest.raises(httpx.HTTPStatusError):
        client.get_latest_release("private-org", "private-repo")


def test_cache_avoids_second_network_call(tmp_path):
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json={"tag_name": "v4.0.0"})

    client = make_client(tmp_path, handler)
    client.get_latest_release("actions", "cache")
    client.get_latest_release("actions", "cache")
    assert calls["count"] == 1


def test_404_is_also_cached(tmp_path):
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(404)

    client = make_client(tmp_path, handler)
    client.get_latest_release("zaproxy", "action-baseline")
    client.get_latest_release("zaproxy", "action-baseline")
    assert calls["count"] == 1


def test_resolve_token_prefers_explicit(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    assert resolve_github_token("explicit-token") == "explicit-token"


def test_resolve_token_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    assert resolve_github_token(None) == "env-token"


def test_resolve_token_falls_back_to_gh_cli(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    class FakeResult:
        returncode = 0
        stdout = "gh-cli-token\n"

    monkeypatch.setattr("subprocess.run", lambda *a, **k: FakeResult())
    assert resolve_github_token(None) == "gh-cli-token"


def test_resolve_token_anonymous_when_gh_not_installed(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def raise_fnf(*args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr("subprocess.run", raise_fnf)
    assert resolve_github_token(None) is None
