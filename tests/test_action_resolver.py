"""Tests de action_resolver.py — cas réels tirés du workflow d'exemple."""

import httpx
import pytest

from gha_audit.models import ActionUsage, RefKind, YamlPath
from gha_audit.parser.ref_classifier import classify_ref, parse_uses
from gha_audit.resolver.action_resolver import resolve_action_version, resolve_all
from gha_audit.resolver.cache import DiskCache
from gha_audit.resolver.github_client import GitHubClient, RateLimitExceeded


def _action_from_raw(raw: str) -> ActionUsage:
    parsed = parse_uses(raw)
    return ActionUsage(
        file_path="ci.yml",
        owner=parsed.owner,
        repo=parsed.repo,
        subpath=parsed.subpath,
        ref=parsed.ref,
        raw=raw,
        yaml_path=YamlPath(("jobs", "build", "steps", 0, "uses")),
        kind=classify_ref(parsed),
    )


def make_client(tmp_path, handler) -> GitHubClient:
    cache = DiskCache(tmp_path / "cache.json", ttl_seconds=3600)
    return GitHubClient(cache=cache, transport=httpx.MockTransport(handler))


def test_semver_major_resolves_via_releases_latest(tmp_path):
    """actions/checkout@v4 -> releases/latest répond, chemin nominal."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/actions/checkout/releases/latest"
        return httpx.Response(200, json={"tag_name": "v5.0.0"})

    action = _action_from_raw("actions/checkout@v4")
    client = make_client(tmp_path, handler)
    resolved = resolve_action_version(client, action)

    assert resolved.latest == "v5.0.0"
    assert resolved.source == "releases_latest"
    assert resolved.confidence == "high"


def test_semver_full_falls_back_to_tags_when_no_release(tmp_path):
    """zaproxy/action-baseline@v0.10.0 -> pas de Release formelle, fallback tags."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/zaproxy/action-baseline/releases/latest":
            return httpx.Response(404)
        if request.url.path == "/repos/zaproxy/action-baseline/tags":
            return httpx.Response(200, json=[{"name": "v0.10.0"}, {"name": "v0.11.0"}, {"name": "v0.9.0"}])
        raise AssertionError(f"Unexpected call: {request.url.path}")

    action = _action_from_raw("zaproxy/action-baseline@v0.10.0")
    client = make_client(tmp_path, handler)
    resolved = resolve_action_version(client, action)

    assert resolved.latest == "v0.11.0"
    assert resolved.source == "tags_sorted"
    assert resolved.confidence == "medium"


def test_floating_branch_still_gets_resolved_for_display(tmp_path):
    """securego/gosec@master -> FLOATING mais on résout quand même la
    dernière version connue (utile pour l'affichage), le statut final
    FLOATING étant décidé ailleurs (audit/comparator.py)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tag_name": "v2.21.0"})

    action = _action_from_raw("securego/gosec@master")
    assert action.kind is RefKind.FLOATING_BRANCH
    client = make_client(tmp_path, handler)
    resolved = resolve_action_version(client, action)

    assert resolved.latest == "v2.21.0"
    assert resolved.source == "releases_latest"


def test_non_github_release_never_calls_api(tmp_path):
    """honnef.co/go/tools/cmd/staticcheck@latest -> aucun appel réseau."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Ne devrait jamais appeler l'API pour un module hors GitHub Releases")

    action = _action_from_raw("honnef.co/go/tools/cmd/staticcheck@latest")
    client = make_client(tmp_path, handler)
    resolved = resolve_action_version(client, action)

    assert resolved.latest is None
    assert resolved.source == "unresolvable"
    assert resolved.confidence == "low"


def test_pinned_sha_never_calls_api(tmp_path):
    sha = "a" * 40

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Ne devrait jamais appeler l'API pour un SHA pinné")

    action = _action_from_raw(f"actions/checkout@{sha}")
    client = make_client(tmp_path, handler)
    resolved = resolve_action_version(client, action)

    assert resolved.latest == sha
    assert resolved.source == "pinned_sha"


def test_no_release_and_no_semver_tags_is_unresolvable(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if "releases/latest" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(200, json=[{"name": "not-a-version"}, {"name": "nightly"}])

    action = _action_from_raw("someorg/weird-action@v1")
    client = make_client(tmp_path, handler)
    resolved = resolve_action_version(client, action)

    assert resolved.latest is None
    assert resolved.source == "unresolvable"


def test_resolve_all_deduplicates_by_slug(tmp_path):
    """Deux occurrences de actions/checkout (deux fichiers/steps différents)
    ne doivent déclencher qu'une seule résolution."""
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json={"tag_name": "v5.0.0"})

    actions = [
        _action_from_raw("actions/checkout@v4"),
        _action_from_raw("actions/checkout@v3"),  # même slug, ref différente
    ]
    client = make_client(tmp_path, handler)
    resolved = resolve_all(client, actions)

    assert calls["count"] == 1
    assert resolved["actions/checkout"].latest == "v5.0.0"


def test_resolve_all_on_full_workflow_action_set(tmp_path):
    """Les 12 actions du workflow d'exemple, résolues en une passe."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/releases/latest"):
            if "zaproxy" in path:
                return httpx.Response(404)
            return httpx.Response(200, json={"tag_name": "v9.9.9"})
        if path.endswith("/tags"):
            return httpx.Response(200, json=[{"name": "v0.10.0"}, {"name": "v0.11.0"}])
        raise AssertionError(f"Unexpected call: {path}")

    raws = [
        "actions/checkout@v4",
        "actions/setup-go@v5",
        "actions/cache@v4",
        "codecov/codecov-action@v4",
        "securego/gosec@master",
        "honnef.co/go/tools/cmd/staticcheck@latest",
        "actions/setup-node@v4",
        "docker/setup-buildx-action@v3",
        "docker/build-push-action@v5",
        "zaproxy/action-baseline@v0.10.0",
        "aquasecurity/trivy-action@master",
        "github/codeql-action/upload-sarif@v3",
    ]
    actions = [_action_from_raw(raw) for raw in raws]
    client = make_client(tmp_path, handler)
    resolved = resolve_all(client, actions)

    assert len(resolved) == 12  # 12 slugs distincts
    assert resolved["honnef.co/go"].source == "unresolvable"
    assert resolved["zaproxy/action-baseline"].latest == "v0.11.0"
    assert resolved["actions/checkout"].latest == "v9.9.9"


# --- Régression : une erreur réseau sur UNE action ne doit jamais faire
# échouer tout le scan (trouvé en usage réel : redirection 301 non suivie
# sur azure/trusted-signing-action a fait planter tout un scan-org).


def test_resolve_action_version_network_error_returns_unresolvable_not_exception(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    action = _action_from_raw("someorg/flaky-repo@v1")
    client = make_client(tmp_path, handler)
    resolved = resolve_action_version(client, action)

    assert resolved.latest is None
    assert resolved.source == "unresolvable"
    assert "someorg/flaky-repo" in resolved.note


def test_resolve_all_continues_after_one_action_network_error(tmp_path):
    """Le cœur de la régression : deux actions, une seule en échec réseau —
    l'autre doit quand même être résolue normalement."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "flaky-repo" in request.url.path:
            raise httpx.ConnectError("unreachable", request=request)
        return httpx.Response(200, json={"tag_name": "v5.0.0"})

    actions = [_action_from_raw("someorg/flaky-repo@v1"), _action_from_raw("actions/checkout@v4")]
    client = make_client(tmp_path, handler)
    resolved = resolve_all(client, actions)

    assert resolved["someorg/flaky-repo"].source == "unresolvable"
    assert resolved["actions/checkout"].latest == "v5.0.0"


def test_resolve_action_version_rate_limit_still_propagates(tmp_path):
    """RateLimitExceeded n'est PAS absorbé comme les autres erreurs réseau :
    c'est un état de session compromis, pas un problème isolé à une action."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"X-RateLimit-Remaining": "0"})

    action = _action_from_raw("actions/checkout@v4")
    client = make_client(tmp_path, handler)

    with pytest.raises(RateLimitExceeded):
        resolve_action_version(client, action)
