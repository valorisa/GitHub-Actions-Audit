"""Tests de classification — un cas par action du workflow d'exemple."""

import pytest

from gha_audit.models import RefKind
from gha_audit.parser.ref_classifier import classify_ref, parse_uses

# (raw, owner, repo, subpath, ref, is_github_action, expected_kind)
CASES = [
    ("actions/checkout@v4", "actions", "checkout", None, "v4", True, RefKind.SEMVER_MAJOR),
    ("actions/setup-go@v5", "actions", "setup-go", None, "v5", True, RefKind.SEMVER_MAJOR),
    ("actions/cache@v4", "actions", "cache", None, "v4", True, RefKind.SEMVER_MAJOR),
    ("codecov/codecov-action@v4", "codecov", "codecov-action", None, "v4", True, RefKind.SEMVER_MAJOR),
    ("securego/gosec@master", "securego", "gosec", None, "master", True, RefKind.FLOATING_BRANCH),
    (
        "honnef.co/go/tools/cmd/staticcheck@latest",
        "honnef.co",
        "go",
        "tools/cmd/staticcheck",
        "latest",
        False,
        RefKind.NON_GITHUB_RELEASE,
    ),
    ("actions/setup-node@v4", "actions", "setup-node", None, "v4", True, RefKind.SEMVER_MAJOR),
    ("docker/setup-buildx-action@v3", "docker", "setup-buildx-action", None, "v3", True, RefKind.SEMVER_MAJOR),
    ("docker/build-push-action@v5", "docker", "build-push-action", None, "v5", True, RefKind.SEMVER_MAJOR),
    (
        "zaproxy/action-baseline@v0.10.0",
        "zaproxy",
        "action-baseline",
        None,
        "v0.10.0",
        True,
        RefKind.SEMVER_FULL,
    ),
    ("aquasecurity/trivy-action@master", "aquasecurity", "trivy-action", None, "master", True, RefKind.FLOATING_BRANCH),
    (
        "github/codeql-action/upload-sarif@v3",
        "github",
        "codeql-action",
        "upload-sarif",
        "v3",
        True,
        RefKind.SEMVER_MAJOR,
    ),
]


@pytest.mark.parametrize("raw,owner,repo,subpath,ref,is_gh,expected_kind", CASES)
def test_parse_and_classify(raw, owner, repo, subpath, ref, is_gh, expected_kind):
    parsed = parse_uses(raw)
    assert parsed.owner == owner
    assert parsed.repo == repo
    assert parsed.subpath == subpath
    assert parsed.ref == ref
    assert parsed.is_github_action is is_gh
    assert classify_ref(parsed) is expected_kind


def test_pinned_sha_classified_regardless_of_repo():
    parsed = parse_uses("actions/checkout@" + "a" * 40)
    assert classify_ref(parsed) is RefKind.PINNED_SHA


def test_unparsable_ref_raises():
    with pytest.raises(ValueError):
        parse_uses("not-a-valid-uses-string")


def test_unknown_branch_name_defaults_to_floating():
    """Un ref non reconnu (ni SHA, ni semver) doit être classé FLOATING,
    jamais silencieusement considéré comme à jour — c'est le choix de
    conception le plus sûr documenté dans l'architecture."""
    parsed = parse_uses("someorg/some-action@feature/experimental")
    assert classify_ref(parsed) is RefKind.FLOATING_BRANCH
