"""Orchestration du pipeline complet — l'"AuditService" du projet.

Discovery -> Parser -> Resolver -> Comparator -> AuditItem

Ce module ne décide jamais rien lui-même (pas de `if status == OUTDATED`,
pas de formatage) — il se contente d'enchaîner les étapes déjà validées
individuellement par leurs propres suites de tests, et de les câbler
ensemble avec le bon ordre de dépendances. C'est le "premier utilisateur"
du moteur, au sens propre : c'est ici que d'éventuelles hypothèses fausses
entre deux modules apparaîtraient.

cli.py ne doit jamais appeler discovery/parser/resolver/comparator
directement — toujours via ce module, pour que la logique d'orchestration
reste testable indépendamment de tout argument de ligne de commande.
"""

from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAMLError

from gha_audit.audit.comparator import build_audit_items
from gha_audit.config import GhaAuditConfig
from gha_audit.discovery.base import WorkflowSource
from gha_audit.discovery.local_discovery import load_local_sources
from gha_audit.discovery.remote_discovery import load_remote_sources
from gha_audit.models import ActionUsage, AuditItem, ParseError, RuntimeVar
from gha_audit.parser.workflow_parser import (
    extract_actions,
    extract_go_installs,
    extract_runtime_vars,
    load_workflow_from_text,
)
from gha_audit.resolver.action_resolver import resolve_all
from gha_audit.resolver.cache import DiskCache
from gha_audit.resolver.github_client import GitHubClient, resolve_github_token
from gha_audit.resolver.runtime_resolver import RuntimeClient, resolve_all_runtimes


def extract_all(
    sources: list[WorkflowSource], config: GhaAuditConfig
) -> tuple[list[ActionUsage], list[RuntimeVar], list[ParseError]]:
    """Parse toutes les sources (locales ou distantes) en une seule passe.

    Étape purement CPU, aucun réseau. Chaque source est isolée : un YAML
    ambigu ou malformé (ex: un `name:` avec un ':' non échappé — un cas
    réel rencontré en usage, pas hypothétique) est écarté et consigné en
    ParseError, sans jamais faire échouer les autres sources du même scan.
    """
    all_actions: list[ActionUsage] = []
    all_runtimes: list[RuntimeVar] = []
    parse_errors: list[ParseError] = []

    for source in sources:
        try:
            data = load_workflow_from_text(source.content)
        except YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            parse_errors.append(
                ParseError(
                    source=source.display_name,
                    message=str(exc),
                    content_preview="\n".join(source.content.splitlines()[:5]),
                    line=(mark.line + 1) if mark is not None else None,
                    column=(mark.column + 1) if mark is not None else None,
                    exception_type=type(exc).__name__,
                )
            )
            continue

        all_actions.extend(extract_actions(data, source.display_name))
        all_actions.extend(extract_go_installs(data, source.display_name))
        all_runtimes.extend(extract_runtime_vars(data, source.display_name, config.known_runtime_vars))

    return all_actions, all_runtimes, parse_errors


def audit_sources(
    sources: list[WorkflowSource],
    config: GhaAuditConfig,
    github_client: GitHubClient,
    runtime_client: RuntimeClient,
) -> tuple[list[AuditItem], list[ParseError]]:
    """Cœur du pipeline : sources déjà chargées -> (AuditItem finaux, erreurs de parsing).

    Les clients sont injectés (jamais construits ici) : c'est ce qui rend
    cette fonction testable avec des transports httpx.MockTransport, sans
    dépendre de run_local_scan/run_remote_scan qui, eux, construisent les
    vrais clients réseau.
    """
    actions, runtimes, parse_errors = extract_all(sources, config)

    action_resolutions = resolve_all(github_client, actions, max_workers=config.max_concurrent_requests)
    runtime_resolutions = resolve_all_runtimes(runtime_client, runtimes)

    items = build_audit_items(actions, action_resolutions, runtimes, runtime_resolutions)
    return items, parse_errors


def _build_clients(config: GhaAuditConfig) -> tuple[GitHubClient, RuntimeClient]:
    """Construit les clients réseau réels, à partir de la config résolue.

    Un seul DiskCache partagé entre les deux clients : ils ne collisionnent
    jamais sur les clés (les URLs GitHub et go.dev/nodejs.org ne se
    recoupent pas), et ça évite deux fichiers de cache à gérer.
    """
    cache = DiskCache(config.cache_path, config.cache_ttl_seconds)
    token = resolve_github_token(config.github_token)
    github_client = GitHubClient(cache=cache, token=token)
    runtime_client = RuntimeClient(cache=cache)
    return github_client, runtime_client


def run_local_scan(root: str | Path, config: GhaAuditConfig | None = None) -> tuple[list[AuditItem], list[ParseError]]:
    """Scanne un dossier local (un repo, ou plusieurs côte à côte comme
    ~/Projets) et renvoie (AuditItem résultants, erreurs de parsing)."""
    config = config or GhaAuditConfig()
    sources = load_local_sources(root)
    github_client, runtime_client = _build_clients(config)
    try:
        return audit_sources(sources, config, github_client, runtime_client)
    finally:
        github_client.close()
        runtime_client.close()


def run_remote_scan(
    owner: str,
    config: GhaAuditConfig | None = None,
    include_forks: bool = False,
    include_archived: bool = False,
) -> tuple[list[AuditItem], list[ParseError]]:
    """Scanne tous les repos d'un owner GitHub via l'API, sans clone local."""
    config = config or GhaAuditConfig()
    github_client, runtime_client = _build_clients(config)
    try:
        sources = load_remote_sources(github_client, owner, include_forks, include_archived)
        return audit_sources(sources, config, github_client, runtime_client)
    finally:
        github_client.close()
        runtime_client.close()
