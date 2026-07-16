"""Tests de cli.py — argument parsing, formats de sortie, codes de sortie.

run_local_scan/run_remote_scan sont monkeypatchés : ce module teste
uniquement la couche CLI (parsing, formatage, exit codes), jamais le
pipeline lui-même (déjà couvert par test_pipeline.py). Cohérent avec le
principe "la CLI orchestre, elle ne décide jamais".
"""

import json

from typer.testing import CliRunner

from gha_audit import cli
from gha_audit.models import ComparisonRelation, ComparisonResult, ResolvedVersion, AuditItem, VersionStatus
from gha_audit.resolver.github_client import RateLimitExceeded

runner = CliRunner()


def _item(identifier: str, status: VersionStatus, category: str = "action") -> AuditItem:
    resolved = ResolvedVersion(latest="v2.0.0", source="releases_latest", confidence="high")
    comparison = ComparisonResult(
        is_comparable=True, relation=ComparisonRelation.OLDER, current="v1.0.0", latest="v2.0.0"
    )
    return AuditItem(
        identifier=identifier,
        current="v1.0.0",
        resolved=resolved,
        comparison=comparison,
        status=status,
        file_path="ci.yml",
        category=category,
    )


# --- scan : formats de sortie ----------------------------------------------


def test_scan_console_output_by_default(monkeypatch):
    monkeypatch.setattr(cli, "run_local_scan", lambda path, config: [_item("actions/checkout", VersionStatus.OUTDATED)])

    result = runner.invoke(cli.app, ["scan", "."])

    assert result.exit_code == cli.EXIT_OK
    assert "actions/checkout" in result.stdout
    assert "{" not in result.stdout  # pas du JSON


def test_scan_json_output(monkeypatch):
    monkeypatch.setattr(cli, "run_local_scan", lambda path, config: [_item("actions/checkout", VersionStatus.OUTDATED)])

    result = runner.invoke(cli.app, ["scan", ".", "--json"])

    assert result.exit_code == cli.EXIT_OK
    parsed = json.loads(result.stdout)
    assert parsed["summary"]["total"] == 1


def test_scan_markdown_output(monkeypatch):
    monkeypatch.setattr(cli, "run_local_scan", lambda path, config: [_item("actions/checkout", VersionStatus.OUTDATED)])

    result = runner.invoke(cli.app, ["scan", ".", "--markdown"])

    assert result.exit_code == cli.EXIT_OK
    assert "# Rapport d'audit GitHub Actions" in result.stdout


# --- scan : --fail-on et codes de sortie -----------------------------------


def test_scan_fail_on_triggers_nonzero_exit_when_matched(monkeypatch):
    monkeypatch.setattr(cli, "run_local_scan", lambda path, config: [_item("actions/checkout", VersionStatus.OUTDATED)])

    result = runner.invoke(cli.app, ["scan", ".", "--fail-on", "outdated"])

    assert result.exit_code == cli.EXIT_FINDINGS


def test_scan_fail_on_does_not_trigger_when_no_match(monkeypatch):
    monkeypatch.setattr(cli, "run_local_scan", lambda path, config: [_item("actions/checkout", VersionStatus.UP_TO_DATE)])

    result = runner.invoke(cli.app, ["scan", ".", "--fail-on", "outdated"])

    assert result.exit_code == cli.EXIT_OK


def test_scan_no_fail_on_always_exits_zero_regardless_of_findings(monkeypatch):
    """Comportement par défaut : rapport seul, jamais de rupture de
    pipeline CI sans opt-in explicite via --fail-on."""
    monkeypatch.setattr(cli, "run_local_scan", lambda path, config: [_item("actions/checkout", VersionStatus.OUTDATED)])

    result = runner.invoke(cli.app, ["scan", "."])

    assert result.exit_code == cli.EXIT_OK


def test_scan_invalid_fail_on_value_exits_config_error(monkeypatch):
    monkeypatch.setattr(cli, "run_local_scan", lambda path, config: [])

    result = runner.invoke(cli.app, ["scan", ".", "--fail-on", "totally-invalid-status"])

    assert result.exit_code == cli.EXIT_CONFIG_ERROR


def test_scan_fail_on_multiple_statuses(monkeypatch):
    monkeypatch.setattr(cli, "run_local_scan", lambda path, config: [_item("securego/gosec", VersionStatus.FLOATING)])

    result = runner.invoke(cli.app, ["scan", ".", "--fail-on", "outdated,floating"])

    assert result.exit_code == cli.EXIT_FINDINGS


# --- scan : --quiet ----------------------------------------------------


def test_scan_quiet_suppresses_output_when_no_findings(monkeypatch):
    monkeypatch.setattr(cli, "run_local_scan", lambda path, config: [_item("actions/checkout", VersionStatus.UP_TO_DATE)])

    result = runner.invoke(cli.app, ["scan", ".", "--fail-on", "outdated", "--quiet"])

    assert result.exit_code == cli.EXIT_OK
    assert result.stdout.strip() == ""


def test_scan_quiet_still_shows_output_when_findings_exist(monkeypatch):
    """--quiet masque le bruit quand tout va bien, mais ne cache jamais
    un vrai problème détecté."""
    monkeypatch.setattr(cli, "run_local_scan", lambda path, config: [_item("actions/checkout", VersionStatus.OUTDATED)])

    result = runner.invoke(cli.app, ["scan", ".", "--fail-on", "outdated", "--quiet"])

    assert result.exit_code == cli.EXIT_FINDINGS
    assert "actions/checkout" in result.stdout


# --- scan : gestion des erreurs réseau --------------------------------------


def test_scan_rate_limit_exceeded_exits_network_error(monkeypatch):
    def raise_rate_limit(path, config):
        raise RateLimitExceeded("Rate limit épuisé")

    monkeypatch.setattr(cli, "run_local_scan", raise_rate_limit)

    result = runner.invoke(cli.app, ["scan", "."])

    assert result.exit_code == cli.EXIT_NETWORK_ERROR


# --- scan-org ---------------------------------------------------------


def test_scan_org_basic(monkeypatch):
    captured = {}

    def fake_run_remote_scan(owner, config, include_forks=False, include_archived=False):
        captured["owner"] = owner
        captured["include_forks"] = include_forks
        return [_item("valorisa/stormgrill", VersionStatus.UP_TO_DATE)]

    monkeypatch.setattr(cli, "run_remote_scan", fake_run_remote_scan)

    result = runner.invoke(cli.app, ["scan-org", "--owner", "valorisa"])

    assert result.exit_code == cli.EXIT_OK
    assert captured["owner"] == "valorisa"
    assert captured["include_forks"] is False


def test_scan_org_requires_owner(monkeypatch):
    monkeypatch.setattr(cli, "run_remote_scan", lambda *a, **k: [])
    result = runner.invoke(cli.app, ["scan-org"])
    assert result.exit_code != cli.EXIT_OK


# --- version -----------------------------------------------------------


def test_version_command():
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip()  # une version non vide est affichée
