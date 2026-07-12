"""Tests de discovery/remote_discovery.py — orchestration complète mockée."""

import base64

import httpx

from gha_audit.discovery.remote_discovery import load_remote_sources
from gha_audit.resolver.cache import DiskCache
from gha_audit.resolver.github_client import GitHubClient


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def test_load_remote_sources_skips_forks_by_default(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path

        if path == "/users/valorisa/repos":
            return httpx.Response(
                200,
                json=[
                    {"name": "stormgrill", "fork": False, "archived": False},
                    {"name": "some-fork", "fork": True, "archived": False},
                ],
            )
        if path == "/repos/valorisa/stormgrill/contents/.github/workflows":
            return httpx.Response(
                200,
                json=[{"name": "ci.yml", "path": ".github/workflows/ci.yml", "type": "file"}],
            )
        if path == "/repos/valorisa/stormgrill/contents/.github/workflows/ci.yml":
            return httpx.Response(200, json={"content": _b64("name: CI")})

        raise AssertionError(f"Unexpected call: {path}")

    cache = DiskCache(tmp_path / "cache.json", ttl_seconds=3600)
    client = GitHubClient(cache=cache, transport=httpx.MockTransport(handler))

    sources = load_remote_sources(client, "valorisa")

    assert len(sources) == 1
    source = sources[0]
    assert source.repo_slug == "valorisa/stormgrill"
    assert source.content == "name: CI"
    assert source.is_writable is False  # jamais réécrit en mode API pur


def test_load_remote_sources_skips_archived_by_default(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/users/valorisa/repos":
            return httpx.Response(200, json=[{"name": "old-repo", "fork": False, "archived": True}])
        raise AssertionError(f"Unexpected call for archived repo: {path}")

    cache = DiskCache(tmp_path / "cache.json", ttl_seconds=3600)
    client = GitHubClient(cache=cache, transport=httpx.MockTransport(handler))

    sources = load_remote_sources(client, "valorisa")
    assert sources == []


def test_load_remote_sources_handles_repo_with_no_workflows(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/users/valorisa/repos":
            return httpx.Response(200, json=[{"name": "no-ci-repo", "fork": False, "archived": False}])
        if path == "/repos/valorisa/no-ci-repo/contents/.github/workflows":
            return httpx.Response(404)
        raise AssertionError(f"Unexpected call: {path}")

    cache = DiskCache(tmp_path / "cache.json", ttl_seconds=3600)
    client = GitHubClient(cache=cache, transport=httpx.MockTransport(handler))

    sources = load_remote_sources(client, "valorisa")
    assert sources == []
