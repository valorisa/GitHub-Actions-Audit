"""Tests des endpoints repos/contents (découverte distante)."""

import base64

import httpx

from gha_audit.resolver.cache import DiskCache
from gha_audit.resolver.github_client import GitHubClient


def make_client(tmp_path, handler) -> GitHubClient:
    cache = DiskCache(tmp_path / "cache.json", ttl_seconds=3600)
    transport = httpx.MockTransport(handler)
    return GitHubClient(cache=cache, transport=transport)


def test_list_repos_for_owner_single_page(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("page") == "1"
        return httpx.Response(200, json=[{"name": "stormgrill"}, {"name": "loop-advisor"}])

    client = make_client(tmp_path, handler)
    repos = client.list_repos_for_owner("valorisa")
    assert [r["name"] for r in repos] == ["stormgrill", "loop-advisor"]


def test_list_repos_for_owner_paginates_until_short_page(tmp_path):
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        page = request.url.params.get("page")
        if page == "1":
            return httpx.Response(200, json=[{"name": f"repo{i}"} for i in range(100)])
        return httpx.Response(200, json=[{"name": "last-repo"}])

    client = make_client(tmp_path, handler)
    repos = client.list_repos_for_owner("valorisa", per_page=100)
    assert len(repos) == 101
    assert calls["count"] == 2


def test_list_workflow_files_filters_to_yaml_only(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"name": "ci.yml", "path": ".github/workflows/ci.yml", "type": "file"},
                {"name": "README.md", "path": ".github/workflows/README.md", "type": "file"},
                {"name": "subfolder", "path": ".github/workflows/subfolder", "type": "dir"},
            ],
        )

    client = make_client(tmp_path, handler)
    files = client.list_workflow_files("valorisa", "stormgrill")
    assert [f["name"] for f in files] == ["ci.yml"]


def test_list_workflow_files_missing_dir_returns_empty(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = make_client(tmp_path, handler)
    assert client.list_workflow_files("valorisa", "no-ci-repo") == []


def test_get_file_content_decodes_base64(tmp_path):
    raw_yaml = "name: CI\non: push"
    encoded = base64.b64encode(raw_yaml.encode("utf-8")).decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": encoded, "encoding": "base64"})

    client = make_client(tmp_path, handler)
    content = client.get_file_content("valorisa", "stormgrill", ".github/workflows/ci.yml")
    assert content == raw_yaml


def test_get_file_content_missing_file_returns_none(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = make_client(tmp_path, handler)
    assert client.get_file_content("valorisa", "stormgrill", "nope.yml") is None
