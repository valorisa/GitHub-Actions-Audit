"""Tests de pipeline.py — le premier vrai test d'intégration bout-en-bout.

C'est ici que d'éventuelles fausses hypothèses entre modules apparaîtraient
(cf. discussion d'architecture) : discovery -> parser -> resolver ->
comparator enchaînés pour de vrai, avec seulement le réseau mocké.
"""

from pathlib import Path

import httpx

from gha_audit import pipeline
from gha_audit.config import GhaAuditConfig
from gha_audit.discovery.base import WorkflowSource
from gha_audit.models import VersionStatus
from gha_audit.resolver.cache import DiskCache
from gha_audit.resolver.github_client import GitHubClient
from gha_audit.resolver.runtime_resolver import RuntimeClient

FIXTURE = Path(__file__).parent / "fixtures" / "sample_workflow.yml"


def _combined_handler(request: httpx.Request) -> httpx.Response:
    """Un seul handler pour les trois hôtes (GitHub, go.dev, nodejs.org) —
    le pipeline réel n'a qu'un DiskCache mais deux clients distincts,
    chacun avec ce transport."""
    host = request.url.host
    path = request.url.path

    if host == "api.github.com":
        if path == "/repos/zaproxy/action-baseline/releases/latest":
            return httpx.Response(404)
        if path == "/repos/zaproxy/action-baseline/tags":
            return httpx.Response(200, json=[{"name": "v0.10.0"}, {"name": "v0.11.0"}])
        if path == "/users/valorisa/repos":
            return httpx.Response(200, json=[{"name": "myrepo", "fork": False, "archived": False}])
        if path == "/repos/valorisa/myrepo/contents/.github/workflows":
            return httpx.Response(
                200, json=[{"name": "ci.yml", "path": ".github/workflows/ci.yml", "type": "file"}]
            )
        if path == "/repos/valorisa/myrepo/contents/.github/workflows/ci.yml":
            import base64

            content = FIXTURE.read_text(encoding="utf-8")
            return httpx.Response(200, json={"content": base64.b64encode(content.encode()).decode()})
        if path.endswith("/releases/latest"):
            return httpx.Response(200, json={"tag_name": "v99.0.0"})
        raise AssertionError(f"Unexpected GitHub path: {path}")

    if host == "go.dev":
        return httpx.Response(200, json=[{"version": "go1.22.5", "stable": True}])

    if host == "nodejs.org":
        return httpx.Response(200, json=[{"version": "v20.11.0", "lts": "Iron"}])

    raise AssertionError(f"Unexpected host: {host}")


def _mocked_clients(tmp_path) -> tuple[GitHubClient, RuntimeClient]:
    cache = DiskCache(tmp_path / "cache.json", ttl_seconds=3600)
    transport = httpx.MockTransport(_combined_handler)
    return GitHubClient(cache=cache, transport=transport), RuntimeClient(cache=cache, transport=transport)


# --- extract_all (pas de réseau) ------------------------------------------


def test_extract_all_on_fixture_workflow():
    content = FIXTURE.read_text(encoding="utf-8")
    sources = [WorkflowSource(display_name="ci.yml", content=content, local_path=FIXTURE)]
    config = GhaAuditConfig()

    actions, runtimes, parse_errors = pipeline.extract_all(sources, config)

    assert len(actions) == 12  # 11 `uses:` + le go install extrait séparément... voir workflow_parser
    assert len(runtimes) == 2
    assert parse_errors == []


# --- audit_sources : cœur injectable, clients mockés -----------------------


def test_audit_sources_full_pipeline_on_fixture_workflow(tmp_path):
    content = FIXTURE.read_text(encoding="utf-8")
    sources = [WorkflowSource(display_name="ci.yml", content=content, local_path=FIXTURE)]
    config = GhaAuditConfig()
    github_client, runtime_client = _mocked_clients(tmp_path)

    items, parse_errors = pipeline.audit_sources(sources, config, github_client, runtime_client)

    assert parse_errors == []

    assert len(items) == 14  # 12 actions + 2 runtimes
    by_id = {item.identifier: item for item in items}

    # Le fallback tags pour zaproxy fonctionne bout-en-bout
    assert by_id["zaproxy/action-baseline"].status is VersionStatus.OUTDATED
    assert by_id["zaproxy/action-baseline"].resolved.source == "tags_sorted"

    # Les branches flottantes restent flottantes malgré la résolution
    assert by_id["securego/gosec"].status is VersionStatus.FLOATING
    assert by_id["aquasecurity/trivy-action"].status is VersionStatus.FLOATING

    # Le module Go hors écosystème GitHub reste non résolvable/unpinned
    assert by_id["honnef.co/go"].status is VersionStatus.UNPINNED

    # Les runtimes sont résolus via les bonnes APIs (go.dev / nodejs.org)
    assert by_id["GO_VERSION"].resolved.source == "go_dl"
    assert by_id["NODE_VERSION"].resolved.source == "node_dist"
    assert by_id["GO_VERSION"].status is VersionStatus.OUTDATED
    assert by_id["NODE_VERSION"].status is VersionStatus.OUTDATED

    # Tous les statuts renvoyés sont des VersionStatus valides (aucune
    # exception silencieuse, aucun None qui aurait fuité)
    for item in items:
        assert isinstance(item.status, VersionStatus)


# --- run_local_scan : wiring discovery locale + clients réels (mockés) ----


def test_run_local_scan_wires_discovery_to_pipeline(tmp_path, monkeypatch):
    repo = tmp_path / "myrepo"
    workflows_dir = repo / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "ci.yml").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    def fake_build_clients(config):
        return _mocked_clients(tmp_path)

    monkeypatch.setattr(pipeline, "_build_clients", fake_build_clients)

    items, parse_errors = pipeline.run_local_scan(repo)
    assert len(items) == 14
    assert parse_errors == []


def test_run_local_scan_on_multiple_sibling_repos(tmp_path, monkeypatch):
    for name in ("repo-a", "repo-b"):
        workflows_dir = tmp_path / name / ".github" / "workflows"
        workflows_dir.mkdir(parents=True)
        (workflows_dir / "ci.yml").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    def fake_build_clients(config):
        return _mocked_clients(tmp_path)

    monkeypatch.setattr(pipeline, "_build_clients", fake_build_clients)

    items, parse_errors = pipeline.run_local_scan(tmp_path)
    assert len(items) == 28  # 14 items x 2 repos
    assert parse_errors == []


# --- run_remote_scan : wiring discovery distante + clients réels (mockés) -


def test_run_remote_scan_wires_discovery_to_pipeline(tmp_path, monkeypatch):
    def fake_build_clients(config):
        return _mocked_clients(tmp_path)

    monkeypatch.setattr(pipeline, "_build_clients", fake_build_clients)

    items, parse_errors = pipeline.run_remote_scan("valorisa")
    assert len(items) == 14
    assert parse_errors == []


# --- Régression : un YAML malformé ne doit jamais faire échouer tout le scan
#
# Trouvé en usage réel (scan-org sur un vrai compte GitHub) : un fichier de
# workflow avec un `name:` contenant un ':' non échappé (ex: "name: Deploy:
# Production") fait planter le parser ruamel round-trip (ScannerError:
# "mapping values are not allowed here"). Avant ce correctif, une seule
# source dans cet état faisait échouer tout `scan-org`, y compris les repos
# sains scannés dans le même batch.

_MALFORMED_WORKFLOW = """name: Deploy: Production
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""


def test_extract_all_isolates_unparseable_source_without_crashing():
    good_content = FIXTURE.read_text(encoding="utf-8")
    sources = [
        WorkflowSource(display_name="good.yml", content=good_content, local_path=FIXTURE),
        WorkflowSource(display_name="broken.yml", content=_MALFORMED_WORKFLOW, local_path=None),
    ]
    config = GhaAuditConfig()

    actions, runtimes, parse_errors = pipeline.extract_all(sources, config)

    # La source valide est traitée normalement malgré l'échec de l'autre.
    assert len(actions) == 12
    assert len(runtimes) == 2

    # La source cassée est signalée, pas silencieusement perdue.
    assert len(parse_errors) == 1
    assert parse_errors[0].source == "broken.yml"
    assert "mapping values" in parse_errors[0].message.lower()
    assert "Deploy: Production" in parse_errors[0].content_preview
    assert parse_errors[0].line is not None
    assert parse_errors[0].column is not None
    assert parse_errors[0].exception_type == "ScannerError"


def test_audit_sources_still_completes_with_one_broken_source(tmp_path):
    good_content = FIXTURE.read_text(encoding="utf-8")
    sources = [
        WorkflowSource(display_name="good.yml", content=good_content, local_path=FIXTURE),
        WorkflowSource(display_name="broken.yml", content=_MALFORMED_WORKFLOW, local_path=None),
    ]
    config = GhaAuditConfig()
    github_client, runtime_client = _mocked_clients(tmp_path)

    items, parse_errors = pipeline.audit_sources(sources, config, github_client, runtime_client)

    assert len(items) == 14  # les 14 items de la source saine, malgré l'autre en échec
    assert len(parse_errors) == 1
    assert parse_errors[0].source == "broken.yml"


def test_extract_all_with_only_broken_sources_returns_empty_but_no_exception():
    sources = [WorkflowSource(display_name="broken.yml", content=_MALFORMED_WORKFLOW, local_path=None)]
    config = GhaAuditConfig()

    actions, runtimes, parse_errors = pipeline.extract_all(sources, config)

    assert actions == []
    assert runtimes == []
    assert len(parse_errors) == 1
