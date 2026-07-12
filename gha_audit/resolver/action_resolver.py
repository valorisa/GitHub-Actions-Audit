"""Résolution de la dernière version stable d'une action, selon son RefKind.

Trois chemins de résolution distincts, dans cet ordre de préférence :
1. `releases/latest` — le plus fiable, quand l'action publie des Releases
   GitHub formelles (la majorité des actions populaires).
2. Fallback tags triés (semver) — pour les actions qui ne publient que des
   tags Git sans jamais créer de Release (ex: zaproxy/action-baseline).
3. Non résolvable — jamais deviné : un module hors écosystème GitHub
   Releases (RefKind.NON_GITHUB_RELEASE) n'est même pas interrogé, et
   l'absence de release/tag exploitable est signalée explicitement plutôt
   que de risquer un faux résultat.

Important : ce module ne décide PAS du statut final (obsolète/à jour/
flottant) — c'est la responsabilité de audit/comparator.py. Ici, on se
contente de répondre à la question "quelle est la dernière version stable
connue ?", y compris pour une action flottante (utile pour l'affichage :
"actuel: master, dernière connue: v2.3.1").
"""

from __future__ import annotations

import re

from gha_audit.models import ActionUsage, RefKind, ResolvedVersion
from gha_audit.resolver.github_client import GitHubClient

# Tags candidats à un tri semver : v1, v1.2, v1.2.3 (avec ou sans préfixe v).
_SEMVER_TAG_RE = re.compile(r"^v?\d+(\.\d+){0,2}$")


def _semver_sort_key(tag: str) -> tuple[int, ...]:
    """Clé de tri numérique pour un tag semver-like (v1.2.3 -> (1, 2, 3))."""
    return tuple(int(part) for part in tag.lstrip("v").split("."))


def _resolve_via_tags(client: GitHubClient, owner: str, repo: str) -> ResolvedVersion:
    """Fallback : trie les tags Git bruts quand il n'y a pas de Release formelle."""
    tags = client.get_tags(owner, repo)
    semver_tags = [t["name"] for t in tags if _SEMVER_TAG_RE.match(t.get("name", ""))]

    if not semver_tags:
        return ResolvedVersion(
            latest=None,
            source="unresolvable",
            confidence="low",
            note="Aucune release ni tag semver exploitable trouvé sur ce dépôt.",
        )

    latest = max(semver_tags, key=_semver_sort_key)
    return ResolvedVersion(
        latest=latest,
        source="tags_sorted",
        confidence="medium",
        note="Pas de Release GitHub formelle : résolu via les tags Git triés.",
    )


def resolve_action_version(client: GitHubClient, action: ActionUsage) -> ResolvedVersion:
    """Résout la dernière version stable connue pour une ActionUsage.

    Ne fait aucun appel réseau pour PINNED_SHA (rien à résoudre : le SHA
    EST la version) ni pour NON_GITHUB_RELEASE (hors écosystème, deviner
    serait pire que de ne rien dire).
    """
    if action.kind is RefKind.PINNED_SHA:
        return ResolvedVersion(
            latest=action.ref,
            source="pinned_sha",
            confidence="high",
            note="SHA pinné : jamais comparé par version (voir --check-sha-age).",
        )

    if action.kind is RefKind.NON_GITHUB_RELEASE:
        return ResolvedVersion(
            latest=None,
            source="unresolvable",
            confidence="low",
            note="Hors écosystème GitHub Releases (ex: module Go) — pin manuel recommandé.",
        )

    release = client.get_latest_release(action.owner, action.repo)
    if release and release.get("tag_name"):
        return ResolvedVersion(latest=release["tag_name"], source="releases_latest", confidence="high")

    return _resolve_via_tags(client, action.owner, action.repo)


def resolve_all(client: GitHubClient, actions: list[ActionUsage]) -> dict[str, ResolvedVersion]:
    """Résout une liste d'actions, dédupliquée par `slug` (owner/repo).

    Deux occurrences du même `owner/repo` (deux fichiers différents, ou
    deux steps du même workflow) ne déclenchent qu'une seule résolution —
    le cache du GitHubClient l'aurait de toute façon évité côté réseau,
    mais dédupliquer ici évite même le travail de tri des tags en double.
    """
    resolved: dict[str, ResolvedVersion] = {}
    for action in actions:
        if action.slug in resolved:
            continue
        resolved[action.slug] = resolve_action_version(client, action)
    return resolved
