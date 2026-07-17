"""CLI — point d'entrée utilisateur du moteur gha_audit.

Aucune logique métier ici : chaque commande construit une GhaAuditConfig,
délègue à pipeline.py (l'orchestration du moteur), puis à audit/report.py
pour le formatage. La CLI orchestre ; elle ne décide jamais un statut ou
une version — c'est déjà tranché avant qu'elle ne reçoive les AuditItem.
"""

from __future__ import annotations

import httpx
import typer

from gha_audit import __version__
from gha_audit.audit.report import format_console, format_json, format_markdown
from gha_audit.config import GhaAuditConfig
from gha_audit.models import AuditItem, VersionStatus
from gha_audit.pipeline import run_local_scan, run_remote_scan
from gha_audit.resolver.github_client import RateLimitExceeded

app = typer.Typer(help="Audit et mise à jour des versions dans les workflows GitHub Actions.")

# Codes de sortie explicites : pensés pour `gha-audit scan . || exit $?`
# dans un pipeline CI, où chaque cas doit être distinguable.
EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_INTERNAL_ERROR = 2
EXIT_NETWORK_ERROR = 3
EXIT_CONFIG_ERROR = 4

_VALID_FAIL_ON = {s.value for s in VersionStatus}


def _parse_fail_on(value: str | None) -> set[VersionStatus]:
    """Parse '--fail-on outdated,floating' en set(VersionStatus).

    Chaîne vide/absente -> set() -> jamais d'exit non-zéro pour un
    "finding" (comportement par défaut : rapport seul, pas de rupture de
    pipeline CI sans opt-in explicite).
    """
    if not value:
        return set()

    requested = {part.strip().lower() for part in value.split(",") if part.strip()}
    invalid = requested - _VALID_FAIL_ON
    if invalid:
        typer.secho(
            f"Valeur(s) invalide(s) pour --fail-on : {', '.join(sorted(invalid))}. "
            f"Valides : {', '.join(sorted(_VALID_FAIL_ON))}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=EXIT_CONFIG_ERROR)

    return {VersionStatus(v) for v in requested}


def _render_parse_errors(parse_errors: list) -> None:
    """Toujours affiché sur stderr, même avec --quiet — un fichier ignoré
    silencieusement serait pire qu'un message un peu bavard."""
    for error in parse_errors:
        typer.secho(f"⚠ {error.source} : non parsé ({error.message.splitlines()[0]})", fg=typer.colors.YELLOW, err=True)


def _render(items: list[AuditItem], *, as_json: bool, as_markdown: bool) -> None:
    if as_json:
        typer.echo(format_json(items))
    elif as_markdown:
        typer.echo(format_markdown(items))
    else:
        typer.echo(format_console(items))


def _exit_code_for(items: list[AuditItem], fail_on: set[VersionStatus]) -> int:
    if fail_on and any(item.status in fail_on for item in items):
        return EXIT_FINDINGS
    return EXIT_OK


def _run_with_error_handling(scan_fn) -> list[AuditItem]:
    """Centralise la conversion exceptions réseau -> code de sortie CLI.

    Les exceptions métier (ValueError de parsing, etc.) ne sont PAS
    attrapées ici : elles remontent comme une vraie erreur interne
    (traceback visible), signal qu'il y a un bug à corriger dans le
    moteur plutôt qu'une situation attendue à masquer.
    """
    try:
        return scan_fn()
    except RateLimitExceeded as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=EXIT_NETWORK_ERROR) from exc
    except httpx.HTTPError as exc:
        typer.secho(f"Erreur réseau : {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=EXIT_NETWORK_ERROR) from exc


@app.command()
def scan(
    path: str = typer.Argument(".", help="Dossier à scanner (un repo, ou plusieurs côte à côte, ex: ~/Projets)."),
    json_output: bool = typer.Option(False, "--json", help="Sortie JSON."),
    markdown: bool = typer.Option(False, "--markdown", help="Sortie markdown (corps de PR)."),
    fail_on: str = typer.Option(
        None, "--fail-on", help="Statuts déclenchant un exit non-zéro, ex: outdated,floating."
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Aucune sortie si aucun problème détecté."),
    token: str = typer.Option(None, "--token", help="Token GitHub explicite (sinon GITHUB_TOKEN / gh auth token)."),
) -> None:
    """Scanne un dossier local et audite ses workflows GitHub Actions."""
    statuses = _parse_fail_on(fail_on)
    config = GhaAuditConfig(github_token=token)

    items, parse_errors = _run_with_error_handling(lambda: run_local_scan(path, config))
    _render_parse_errors(parse_errors)

    exit_code = _exit_code_for(items, statuses)
    if not quiet or exit_code != EXIT_OK:
        _render(items, as_json=json_output, as_markdown=markdown)
    raise typer.Exit(code=exit_code)


@app.command("scan-org")
def scan_org(
    owner: str = typer.Option(..., "--owner", help="Compte/organisation GitHub à scanner."),
    include_forks: bool = typer.Option(False, "--include-forks", help="Inclure les forks (exclus par défaut)."),
    include_archived: bool = typer.Option(
        False, "--include-archived", help="Inclure les repos archivés (exclus par défaut)."
    ),
    json_output: bool = typer.Option(False, "--json", help="Sortie JSON."),
    markdown: bool = typer.Option(False, "--markdown", help="Sortie markdown (corps de PR)."),
    fail_on: str = typer.Option(
        None, "--fail-on", help="Statuts déclenchant un exit non-zéro, ex: outdated,floating."
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Aucune sortie si aucun problème détecté."),
    token: str = typer.Option(None, "--token", help="Token GitHub explicite (sinon GITHUB_TOKEN / gh auth token)."),
) -> None:
    """Scanne tous les repos d'un owner GitHub via l'API, sans clone local.

    Lecture seule par nature : aucune option --fix ici (voir ADR sur
    writer/yaml_updater.py, à venir — réécrire sans clone impliquerait de
    committer via l'API Git, hors scope pour l'instant).
    """
    statuses = _parse_fail_on(fail_on)
    config = GhaAuditConfig(github_token=token)

    items, parse_errors = _run_with_error_handling(
        lambda: run_remote_scan(owner, config, include_forks=include_forks, include_archived=include_archived)
    )
    _render_parse_errors(parse_errors)

    exit_code = _exit_code_for(items, statuses)
    if not quiet or exit_code != EXIT_OK:
        _render(items, as_json=json_output, as_markdown=markdown)
    raise typer.Exit(code=exit_code)


@app.command()
def version() -> None:
    """Affiche la version de gha-audit."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
