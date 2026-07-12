"""Tests d'extraction bout-en-bout sur la fixture du workflow d'exemple."""

from pathlib import Path

from gha_audit.models import RefKind
from gha_audit.parser.workflow_parser import (
    extract_actions,
    extract_go_installs,
    extract_runtime_vars,
    load_workflow,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_workflow.yml"


def test_extracts_all_twelve_uses_actions():
    data = load_workflow(FIXTURE)
    actions = extract_actions(data, str(FIXTURE))
    # go install ...@latest n'est PAS un `uses:`, donc 11 actions structurées ici
    assert len(actions) == 11
    slugs = {a.slug for a in actions}
    assert "actions/checkout" in slugs
    assert "github/codeql-action" in slugs


def test_extracts_go_install_separately():
    data = load_workflow(FIXTURE)
    go_installs = extract_go_installs(data, str(FIXTURE))
    assert len(go_installs) == 1
    item = go_installs[0]
    assert item.owner == "honnef.co"
    assert item.kind is RefKind.NON_GITHUB_RELEASE


def test_extracts_runtime_vars():
    data = load_workflow(FIXTURE)
    runtimes = extract_runtime_vars(data, str(FIXTURE))
    values = {r.name: r.value for r in runtimes}
    assert values["GO_VERSION"] == "1.21"
    assert values["NODE_VERSION"] == "18"


def test_floating_and_pinned_refs_correctly_flagged():
    data = load_workflow(FIXTURE)
    actions = extract_actions(data, str(FIXTURE))
    floating = {a.slug for a in actions if a.kind is RefKind.FLOATING_BRANCH}
    assert floating == {"securego/gosec", "aquasecurity/trivy-action"}

    full_semver = {a.slug for a in actions if a.kind is RefKind.SEMVER_FULL}
    assert full_semver == {"zaproxy/action-baseline"}
