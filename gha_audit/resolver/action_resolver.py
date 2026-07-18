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
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from gha_audit.models import ActionUsage, RefKind, ResolvedVersion
from gha_audit.resolver.github_client import GitHubClient, RateLimitExceeded

# Tags candidats à un tri semver : v1, v1.2, v1.2.3 (avec ou sans préfixe v).
_SEMVER_TAG_RE = re.compile(r"^v?\d+(\.\d+){0,2}$")

# Borne par défaut : assez pour masquer la latence réseau (le facteur
# dominant mesuré en usage réel — voir profil scan-org), assez prudent
# pour ne pas menacer le rate limit GitHub (5000/h authentifié) sur un
# grand scan-org. Reste ajustable par l'appelant (voir GhaAuditConfig).
DEFAULT_MAX_WORKERS = 8


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

    Isolation par action (trouvé en usage réel : un repo renommé ou une
    erreur réseau transitoire sur UNE action ne doit jamais faire échouer
    tout un scan-org) : tout httpx.HTTPError devient UNRESOLVABLE plutôt
    que de remonter. RateLimitExceeded fait exception — c'est un état de
    session compromis (le budget API est épuisé pour toute action
    suivante aussi), donc il continue de se propager pour arrêter le scan.
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

    try:
        release = client.get_latest_release(action.owner, action.repo)
        if release and release.get("tag_name"):
            return ResolvedVersion(latest=release["tag_name"], source="releases_latest", confidence="high")
        return _resolve_via_tags(client, action.owner, action.repo)
    except RateLimitExceeded:
        raise
    except httpx.HTTPError as exc:
        return ResolvedVersion(
            latest=None,
            source="unresolvable",
            confidence="low",
            note=f"Erreur réseau lors de la résolution de {action.slug} : {exc}",
        )


def resolve_all(
    client: GitHubClient, actions: list[ActionUsage], max_workers: int = DEFAULT_MAX_WORKERS
) -> dict[str, ResolvedVersion]:
    """Résout une liste d'actions, dédupliquée par `slug` (owner/repo),
    en parallélisant les résolutions avec une concurrence bornée.

    Les résolutions sont indépendantes (aucun partage d'état entre elles
    hors le GitHubClient/DiskCache, thread-safe — voir cache.py), donc
    parallélisables sans changer le résultat final, seulement le temps
    total : c'est le levier de performance mesuré comme le plus rentable
    en usage réel (temps réseau très majoritaire face au temps CPU).

    RateLimitExceeded annule le travail restant (futures non démarrées)
    plutôt que de laisser le pool consommer inutilement le budget API
    déjà épuisé, puis se propage — comportement identique à la version
    séquentielle : la session est compromise, pas une seule action.
    """
    unique_actions: dict[str, ActionUsage] = {}
    for action in actions:
        if action.slug not in unique_actions:
            unique_actions[action.slug] = action

    if not unique_actions:
        return {}

    resolved: dict[str, ResolvedVersion] = {}
    executor = ThreadPoolExecutor(max_workers=max_workers)
    future_to_slug = {
        executor.submit(resolve_action_version, client, action): slug
        for slug, action in unique_actions.items()
    }
    try:
        for future in as_completed(future_to_slug):
            resolved[future_to_slug[future]] = future.result()
    except RateLimitExceeded:
        executor.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)

    return resolved
