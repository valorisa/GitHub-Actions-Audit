"""Tests de audit/comparator.py — module pur, aucun mock réseau nécessaire.

Deux niveaux de tests, reflétant la séparation du module :
1. compare_versions() / classify_action() / classify_runtime() en isolation
   (le cœur de la logique, testé sans avoir besoin d'ActionUsage/RuntimeVar).
2. compare_action() / compare_runtime() / build_audit_items() en bout-en-bout
   (orchestration), sur les cas réels du workflow d'exemple.
"""

from gha_audit.audit.comparator import (
    build_audit_items,
    classify_action,
    classify_runtime,
    compare_action,
    compare_runtime,
    compare_versions,
)
from gha_audit.models import (
    ActionUsage,
    ComparisonRelation,
    RefKind,
    ResolvedVersion,
    RuntimeVar,
    VersionStatus,
    YamlPath,
)
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


def _resolved(latest: str | None, source: str = "releases_latest") -> ResolvedVersion:
    return ResolvedVersion(latest=latest, source=source, confidence="high")


# --- Niveau 1 : compare_versions() en isolation, aucune notion de RefKind ---


def test_compare_versions_identical():
    result = compare_versions("v4.0.0", "v4.0.0")
    assert result.is_comparable is True
    assert result.relation is ComparisonRelation.IDENTICAL


def test_compare_versions_older():
    result = compare_versions("v0.10.0", "v0.11.0")
    assert result.relation is ComparisonRelation.OLDER


def test_compare_versions_newer():
    """Cas rare mais réel : current en avance sur latest (ex: cache API
    en retard). ComparisonResult ne juge pas, il constate."""
    result = compare_versions("v5.0.0", "v4.9.0")
    assert result.relation is ComparisonRelation.NEWER


def test_compare_versions_unknown_when_latest_missing():
    result = compare_versions("v4.0.0", None)
    assert result.is_comparable is False
    assert result.relation is ComparisonRelation.UNKNOWN


def test_compare_versions_unknown_when_current_not_parseable():
    """current='master' (branche) : non comparable, jamais une exception."""
    result = compare_versions("master", "v2.21.0")
    assert result.is_comparable is False
    assert result.relation is ComparisonRelation.UNKNOWN


def test_compare_versions_major_only_ignores_minor_patch_diff():
    """Le cœur du design : v4 vs v4.3.0 en mode major_only -> IDENTICAL,
    alors qu'une comparaison complète dirait OLDER."""
    major_only_result = compare_versions("v4", "v4.3.0", major_only=True)
    full_result = compare_versions("v4", "v4.3.0", major_only=False)

    assert major_only_result.relation is ComparisonRelation.IDENTICAL
    assert full_result.relation is ComparisonRelation.OLDER


def test_compare_versions_major_only_detects_new_major():
    result = compare_versions("v4", "v5.0.0", major_only=True)
    assert result.relation is ComparisonRelation.OLDER


# --- Niveau 1 : classify_action() / classify_runtime(), policy pure ---


def test_classify_action_floating_ignores_comparison_entirely():
    """FLOATING_BRANCH -> FLOATING même avec un ComparisonResult IDENTICAL
    fabriqué de toutes pièces : la policy ne regarde même pas le fait."""
    fake_identical = compare_versions("v1.0.0", "v1.0.0")
    status = classify_action(RefKind.FLOATING_BRANCH, fake_identical)
    assert status is VersionStatus.FLOATING


def test_classify_action_non_github_release_always_unpinned():
    unknown = compare_versions("latest", None)
    status = classify_action(RefKind.NON_GITHUB_RELEASE, unknown)
    assert status is VersionStatus.UNPINNED


def test_classify_action_pinned_sha_always_up_to_date():
    unknown = compare_versions("a" * 40, "a" * 40)  # non comparable (hex, pas numérique)
    status = classify_action(RefKind.PINNED_SHA, unknown)
    assert status is VersionStatus.UP_TO_DATE


def test_classify_action_semver_outdated_on_older_relation():
    older = compare_versions("v0.10.0", "v0.11.0")
    status = classify_action(RefKind.SEMVER_FULL, older)
    assert status is VersionStatus.OUTDATED


def test_classify_action_semver_up_to_date_on_identical():
    identical = compare_versions("v4.0.0", "v4.0.0")
    status = classify_action(RefKind.SEMVER_FULL, identical)
    assert status is VersionStatus.UP_TO_DATE


def test_classify_action_unresolvable_when_not_comparable():
    unknown = compare_versions("v4.0.0", None)
    status = classify_action(RefKind.SEMVER_FULL, unknown)
    assert status is VersionStatus.UNRESOLVABLE


def test_classify_runtime_outdated():
    older = compare_versions("1.21", "1.22.5")
    assert classify_runtime(older) is VersionStatus.OUTDATED


def test_classify_runtime_unresolvable():
    unknown = compare_versions("1.21", None)
    assert classify_runtime(unknown) is VersionStatus.UNRESOLVABLE


# --- Niveau 2 : orchestration haut niveau, cas réels du workflow --------


def test_compare_action_major_tag_up_to_date_when_same_major_even_with_newer_patch():
    action = _action("actions/checkout@v4")
    item = compare_action(action, _resolved("v4.3.0"))
    assert item.status is VersionStatus.UP_TO_DATE
    assert item.comparison.relation is ComparisonRelation.IDENTICAL


def test_compare_action_major_tag_outdated_only_when_new_major_exists():
    action = _action("actions/checkout@v4")
    item = compare_action(action, _resolved("v5.0.0"))
    assert item.status is VersionStatus.OUTDATED
    assert item.comparison.relation is ComparisonRelation.OLDER


def test_compare_action_full_pin_outdated_on_any_component_difference():
    action = _action("zaproxy/action-baseline@v0.10.0")
    item = compare_action(action, _resolved("v0.11.0"))
    assert item.status is VersionStatus.OUTDATED


def test_compare_action_floating_branch_always_floating():
    action = _action("securego/gosec@master")
    item = compare_action(action, _resolved("v2.21.0"))
    assert item.status is VersionStatus.FLOATING
    # Le fait de comparaison existe toujours (transparence pour le rapport),
    # même s'il n'a pas influencé le statut.
    assert item.comparison.is_comparable is False


def test_compare_action_non_github_release_always_unpinned():
    action = _action("honnef.co/go/tools/cmd/staticcheck@latest")
    item = compare_action(action, _resolved(None, source="unresolvable"))
    assert item.status is VersionStatus.UNPINNED


def test_compare_action_pinned_sha_always_up_to_date():
    sha = "a" * 40
    action = _action(f"actions/checkout@{sha}")
    item = compare_action(action, _resolved(sha, source="pinned_sha"))
    assert item.status is VersionStatus.UP_TO_DATE


def test_compare_runtime_outdated():
    runtime = _runtime("GO_VERSION", "1.21")
    item = compare_runtime(runtime, _resolved("1.22.5", source="go_dl"))
    assert item.status is VersionStatus.OUTDATED
    assert item.category == "runtime"


def test_compare_runtime_up_to_date():
    runtime = _runtime("NODE_VERSION", "20.11.0")
    item = compare_runtime(runtime, _resolved("20.11.0", source="node_dist"))
    assert item.status is VersionStatus.UP_TO_DATE


# --- Assemblage complet sur les 12 actions + 2 runtimes du workflow ------


def test_build_audit_items_on_full_workflow_example():
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
        "actions/checkout": _resolved("v5.0.0"),  # nouveau majeur -> OUTDATED
        "actions/setup-go": _resolved("v5.1.0"),  # même majeur -> UP_TO_DATE
        "actions/cache": _resolved("v4.4.0"),
        "codecov/codecov-action": _resolved("v4.2.0"),
        "securego/gosec": _resolved("v2.21.0"),
        "honnef.co/go": _resolved(None, source="unresolvable"),
        "actions/setup-node": _resolved("v4.1.0"),
        "docker/setup-buildx-action": _resolved("v3.5.0"),
        "docker/build-push-action": _resolved("v6.0.0"),  # nouveau majeur -> OUTDATED
        "zaproxy/action-baseline": _resolved("v0.11.0", source="tags_sorted"),
        "aquasecurity/trivy-action": _resolved("v0.24.0"),
        "github/codeql-action": _resolved("v3.26.0"),
    }

    runtimes = [_runtime("GO_VERSION", "1.21"), _runtime("NODE_VERSION", "18")]
    runtime_resolutions = {
        "GO_VERSION": _resolved("1.22.5", source="go_dl"),
        "NODE_VERSION": _resolved("20.11.0", source="node_dist"),
    }

    items = build_audit_items(actions, action_resolutions, runtimes, runtime_resolutions)
    by_id = {item.identifier: item for item in items}

    assert len(items) == 14  # 12 actions + 2 runtimes

    assert by_id["actions/checkout"].status is VersionStatus.OUTDATED
    assert by_id["actions/setup-go"].status is VersionStatus.UP_TO_DATE
    assert by_id["securego/gosec"].status is VersionStatus.FLOATING
    assert by_id["aquasecurity/trivy-action"].status is VersionStatus.FLOATING
    assert by_id["honnef.co/go"].status is VersionStatus.UNPINNED
    assert by_id["docker/build-push-action"].status is VersionStatus.OUTDATED
    assert by_id["zaproxy/action-baseline"].status is VersionStatus.OUTDATED
    assert by_id["GO_VERSION"].status is VersionStatus.OUTDATED
    assert by_id["NODE_VERSION"].status is VersionStatus.OUTDATED


def test_build_audit_items_skips_unmatched_entries_gracefully():
    actions = [_action("actions/checkout@v4")]
    item_list = build_audit_items(actions, {}, [], {})
    assert item_list == []
