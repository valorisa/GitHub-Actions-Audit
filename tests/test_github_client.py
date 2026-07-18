"""Tests du client GitHub — httpx.MockTransport, aucun appel réseau réel."""

import time

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


def test_client_follows_redirects_transparently(tmp_path):
    """Régression : trouvé en usage réel sur azure/trusted-signing-action,
    renommé/déplacé côté GitHub. Un 301 est un cas normal, pas une erreur —
    le client doit suivre la redirection sans lever d'exception."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/azure/trusted-signing-action/releases/latest":
            return httpx.Response(
                301,
                headers={"Location": "https://api.github.com/repositories/771200572/releases/latest"},
            )
        if request.url.path == "/repositories/771200572/releases/latest":
            return httpx.Response(200, json={"tag_name": "v0.5.9"})
        raise AssertionError(f"Unexpected call: {request.url.path}")

    client = make_client(tmp_path, handler)
    result = client.get_latest_release("azure", "trusted-signing-action")
    assert result["tag_name"] == "v0.5.9"


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


# --- Étape 2 du chantier ETag : GET conditionnel, uniquement à l'expiration
# du TTL — le fast-path (aucun appel réseau tant que le TTL est valide)
# reste strictement inchangé, vérifié explicitement ci-dessous.


def test_fresh_ttl_entry_makes_zero_network_calls(tmp_path):
    """Régression du fast-path existant : DEUX résolutions dans la même
    fenêtre TTL ne doivent déclencher qu'UN seul appel réseau, ETag ou pas."""
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json={"tag_name": "v5.0.0"}, headers={"ETag": '"abc123"'})

    client = make_client(tmp_path, handler)
    client.get_latest_release("actions", "checkout")
    client.get_latest_release("actions", "checkout")

    assert calls["count"] == 1


def test_no_if_none_match_header_on_first_ever_request(tmp_path):
    """Aucun ETag connu -> aucun header conditionnel envoyé."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "If-None-Match" not in request.headers
        return httpx.Response(200, json={"tag_name": "v5.0.0"})

    client = make_client(tmp_path, handler)
    client.get_latest_release("actions", "checkout")


def test_if_none_match_sent_when_ttl_expired_and_etag_known(tmp_path):
    """Le cœur du chantier : TTL expiré + ETag connu -> GET conditionnel."""
    cache = DiskCache(tmp_path / "cache.json", ttl_seconds=0)  # expire immédiatement
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(200, json={"tag_name": "v5.0.0"}, headers={"ETag": '"abc123"'})
        assert request.headers.get("If-None-Match") == '"abc123"'
        return httpx.Response(304)

    client = GitHubClient(cache=cache, transport=httpx.MockTransport(handler))
    client.get_latest_release("actions", "checkout")  # premier appel : 200, stocke l'ETag
    time.sleep(0.01)  # garantit l'expiration du TTL=0
    result = client.get_latest_release("actions", "checkout")  # second appel : 304 attendu

    assert calls["count"] == 2
    assert result["tag_name"] == "v5.0.0"


def test_304_reuses_cached_value_without_reparsing_body(tmp_path):
    cache = DiskCache(tmp_path / "cache.json", ttl_seconds=0)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("If-None-Match"):
            return httpx.Response(304)
        return httpx.Response(200, json={"tag_name": "v4.2.0"}, headers={"ETag": '"etag-1"'})

    client = GitHubClient(cache=cache, transport=httpx.MockTransport(handler))
    first = client.get_latest_release("actions", "checkout")
    time.sleep(0.01)
    second = client.get_latest_release("actions", "checkout")

    assert first == second == {"tag_name": "v4.2.0"}


def test_304_refreshes_ttl_so_next_call_hits_fast_path(tmp_path):
    """Après un 304, la fraîcheur est rétablie : l'appel SUIVANT (dans la
    nouvelle fenêtre TTL) ne doit plus toucher le réseau du tout."""
    cache = DiskCache(tmp_path / "cache.json", ttl_seconds=3600)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(200, json={"tag_name": "v5.0.0"}, headers={"ETag": '"abc123"'})
        return httpx.Response(304)

    client = GitHubClient(cache=cache, transport=httpx.MockTransport(handler))
    client.get_latest_release("actions", "checkout")

    # Force artificiellement l'expiration pour déclencher le second appel réseau (304),
    # sans attendre le vrai TTL de 3600s.
    cache._data["/repos/actions/checkout/releases/latest"]["stored_at"] -= 7200
    client.get_latest_release("actions", "checkout")  # -> 304, rafraîchit stored_at
    assert calls["count"] == 2

    # stored_at étant rafraîchi, ce troisième appel doit rester sur le fast-path.
    client.get_latest_release("actions", "checkout")
    assert calls["count"] == 2  # inchangé : aucun nouvel appel réseau


def test_200_after_stale_fully_replaces_value_and_etag(tmp_path):
    """Le serveur a une nouvelle version : le 200 remplace tout, pas de
    fusion partielle avec l'ancienne entrée."""
    cache = DiskCache(tmp_path / "cache.json", ttl_seconds=0)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(200, json={"tag_name": "v5.0.0"}, headers={"ETag": '"old-etag"'})
        assert request.headers.get("If-None-Match") == '"old-etag"'
        return httpx.Response(200, json={"tag_name": "v6.0.0"}, headers={"ETag": '"new-etag"'})

    client = GitHubClient(cache=cache, transport=httpx.MockTransport(handler))
    client.get_latest_release("actions", "checkout")
    time.sleep(0.01)
    result = client.get_latest_release("actions", "checkout")

    assert result["tag_name"] == "v6.0.0"
    entry = cache.get_stale_entry("/repos/actions/checkout/releases/latest")
    assert entry.etag == '"new-etag"'


def test_404_handling_unaffected_by_etag_changes(tmp_path):
    """Le 404 n'est volontairement pas touché à cette étape : toujours
    géré comme avant, sans ETag ni conditionnel."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "If-None-Match" not in request.headers
        return httpx.Response(404)

    client = make_client(tmp_path, handler)
    assert client.get_latest_release("zaproxy", "action-baseline") is None


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
