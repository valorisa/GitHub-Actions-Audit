"""Tests de audit/report.py — sur le même jeu de 14 items que test_comparator.py.

Comptage attendu, vérifié à la main sur le workflow d'exemple :
- OUTDATED (5) : actions/checkout, docker/build-push-action,
  zaproxy/action-baseline, GO_VERSION, NODE_VERSION
- FLOATING (2) : securego/gosec, aquasecurity/trivy-action
- UNPINNED (1) : honnef.co/go
- UP_TO_DATE (6) : actions/setup-go, actions/cache, codecov/codecov-action,
  actions/setup-node, docker/setup-buildx-action, github/codeql-action
- UNRESOLVABLE (0)
"""

import json

from gha_audit.audit.comparator import build_audit_items
from gha_audit.audit.report import build_summary, format_console, format_json, format_markdown
from gha_audit.models import ActionUsage, ResolvedVersion, RuntimeVar, YamlPath
from gha_audit.parser.ref_classifier import classify_ref, parse_uses


def _action(raw: str) -> ActionUsage:
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


def _runtime(name: str, value: str) -> RuntimeVar:
    return RuntimeVar(file_path="ci.yml", name=name, value=value, yaml_path=YamlPath(("env", name)))


def _resolved(latest, source="releases_latest"):
    return ResolvedVersion(latest=latest, source=source, confidence="high")


def _golden_items():
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
    actions = [_action(raw) for raw in raws]
    action_resolutions = {
        "actions/checkout": _resolved("v5.0.0"),
        "actions/setup-go": _resolved("v5.1.0"),
        "actions/cache": _resolved("v4.4.0"),
        "codecov/codecov-action": _resolved("v4.2.0"),
        "securego/gosec": _resolved("v2.21.0"),
        "honnef.co/go": _resolved(None, source="unresolvable"),
        "actions/setup-node": _resolved("v4.1.0"),
        "docker/setup-buildx-action": _resolved("v3.5.0"),
        "docker/build-push-action": _resolved("v6.0.0"),
        "zaproxy/action-baseline": _resolved("v0.11.0", source="tags_sorted"),
        "aquasecurity/trivy-action": _resolved("v0.24.0"),
        "github/codeql-action": _resolved("v3.26.0"),
    }
    runtimes = [_runtime("GO_VERSION", "1.21"), _runtime("NODE_VERSION", "18")]
    runtime_resolutions = {
        "GO_VERSION": _resolved("1.22.5", source="go_dl"),
        "NODE_VERSION": _resolved("20.11.0", source="node_dist"),
    }
    return build_audit_items(actions, action_resolutions, runtimes, runtime_resolutions)


# --- build_summary ---------------------------------------------------------


def test_build_summary_counts_match_golden_workflow():
    items = _golden_items()
    summary = build_summary(items)

    assert summary.total == 14
    assert summary.outdated == 5
    assert summary.floating == 2
    assert summary.unpinned == 1
    assert summary.up_to_date == 6
    assert summary.unresolvable == 0


def test_build_summary_on_empty_list():
    summary = build_summary([])
    assert summary.total == 0
    assert summary.outdated == 0


# --- format_json -------------------------------------------------------


def test_format_json_is_valid_and_parseable():
    items = _golden_items()
    output = format_json(items)
    parsed = json.loads(output)  # doit être un JSON valide, pas d'exception

    assert parsed["summary"]["total"] == 14
    assert parsed["summary"]["outdated"] == 5
    assert len(parsed["items"]) == 14


def test_format_json_item_fields():
    items = _golden_items()
    parsed = json.loads(format_json(items))

    checkout = next(i for i in parsed["items"] if i["identifier"] == "actions/checkout")
    assert checkout["status"] == "outdated"
    assert checkout["current"] == "v4"
    assert checkout["latest"] == "v5.0.0"
    assert checkout["category"] == "action"


def test_format_json_empty_list():
    parsed = json.loads(format_json([]))
    assert parsed["summary"]["total"] == 0
    assert parsed["items"] == []


# --- format_markdown -----------------------------------------------------


def test_format_markdown_contains_summary_and_all_identifiers():
    items = _golden_items()
    output = format_markdown(items)

    assert "14 élément(s)" in output
    assert "5 obsolète(s)" in output
    for item in items:
        assert item.identifier in output


def test_format_markdown_uses_status_icons():
    items = _golden_items()
    output = format_markdown(items)
    assert "🔴" in output  # au moins un OUTDATED
    assert "🟡" in output  # au moins un FLOATING
    assert "🟠" in output  # au moins un UNPINNED


def test_format_markdown_empty_list_does_not_crash():
    output = format_markdown([])
    assert "0 élément(s)" in output
    assert "Aucun élément" in output


# --- format_console --------------------------------------------------------


def test_format_console_contains_all_identifiers_and_summary_line():
    items = _golden_items()
    output = format_console(items)

    assert "outdated=5" in output
    assert "floating=2" in output
    assert "unpinned=1" in output
    assert "up_to_date=6" in output
    for item in items:
        assert item.identifier in output


def test_format_console_empty_list_does_not_crash():
    output = format_console([])
    assert "(aucun élément)" in output
