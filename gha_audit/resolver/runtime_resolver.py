"""Résolution des versions runtime (Go, Node) — sources API distinctes de GitHub.

Contrat en miroir de action_resolver.py (même type de retour ResolvedVersion),
mais deux APIs officielles différentes de l'API GitHub, d'où un client HTTP
séparé et volontairement simple (pas de résolution de release/tag GitHub ici).

Policy Node : comparaison contre la dernière version **LTS**, pas "Current".
Choix assumé : dans un contexte CI, NODE_VERSION est un choix délibéré de
stabilité, pas juste "pas encore mis à jour" — comparer contre Current
générerait un bruit d'audit non pertinent (fausse "obsolescence" à chaque
sortie d'une version Current plus récente que n'importe quelle LTS).
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from gha_audit.models import ResolvedVersion, RuntimeVar
from gha_audit.resolver.cache import MISSING, DiskCache

GO_DL_URL = "https://go.dev/dl/?mode=json"
NODE_DIST_URL = "https://nodejs.org/dist/index.json"

_GO_VERSION_RE = re.compile(r"^go(\d+(?:\.\d+){0,2})")


def _semver_sort_key(version: str) -> tuple[int, ...]:
    """Clé de tri numérique pour une version 'propre' (1.22.0 -> (1, 22, 0)).

    Duplique volontairement la logique équivalente de action_resolver.py :
    les deux modules n'ont aucune raison de dépendre l'un de l'autre, et la
    fonction est triviale — un import croisé coûterait plus qu'il n'apporte.
    """
    return tuple(int(part) for part in version.lstrip("v").split("."))


class RuntimeClient:
    """Wrapper httpx minimal pour les APIs de release Go et Node officielles."""

    def __init__(self, cache: DiskCache, transport: httpx.BaseTransport | None = None) -> None:
        self._cache = cache
        self._client = httpx.Client(transport=transport, timeout=10.0)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> RuntimeClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _get_json(self, url: str) -> Any:
        cached = self._cache.get(url)
        if cached is not MISSING:
            return cached
        response = self._client.get(url)
        response.raise_for_status()
        data = response.json()
        self._cache.set(url, data)
        return data

    def get_go_releases(self) -> list[dict]:
        return self._get_json(GO_DL_URL)

    def get_node_releases(self) -> list[dict]:
        return self._get_json(NODE_DIST_URL)


def resolve_go_version(client: RuntimeClient) -> ResolvedVersion:
    """Dernière version stable Go, via l'API officielle go.dev/dl.

    Ne suppose jamais que l'API renvoie les versions triées : on calcule
    le max explicitement, pour rester robuste à un changement d'ordre.
    """
    try:
        releases = client.get_go_releases()
    except httpx.HTTPError:
        return ResolvedVersion(
            latest=None, source="unresolvable", confidence="low", note="Impossible de joindre go.dev/dl."
        )

    stable_versions = []
    for release in releases:
        if not release.get("stable"):
            continue
        match = _GO_VERSION_RE.match(release.get("version", ""))
        if match:
            stable_versions.append(match.group(1))

    if not stable_versions:
        return ResolvedVersion(
            latest=None,
            source="unresolvable",
            confidence="low",
            note="Aucune version stable trouvée dans la réponse go.dev/dl.",
        )

    latest = max(stable_versions, key=_semver_sort_key)
    return ResolvedVersion(latest=latest, source="go_dl", confidence="high")


def resolve_node_version(client: RuntimeClient, track: str = "lts") -> ResolvedVersion:
    """Dernière version Node connue, filtrée par suivi ('lts' ou 'current')."""
    try:
        releases = client.get_node_releases()
    except httpx.HTTPError:
        return ResolvedVersion(
            latest=None, source="unresolvable", confidence="low", note="Impossible de joindre nodejs.org/dist."
        )

    candidates = [r for r in releases if r.get("lts")] if track == "lts" else releases

    if not candidates:
        return ResolvedVersion(
            latest=None,
            source="unresolvable",
            confidence="low",
            note=f"Aucune version Node trouvée pour le suivi '{track}'.",
        )

    best = max(candidates, key=lambda r: _semver_sort_key(r.get("version", "v0")))
    version = best.get("version", "").lstrip("v")
    note = f"Suivi LTS ({best.get('lts')})" if track == "lts" else "Suivi Current (dernière stable absolue)"
    return ResolvedVersion(latest=version, source="node_dist", confidence="high", note=note)


# Dispatch par nom de variable — même principe que DEFAULT_RUNTIME_VAR_NAMES
# dans workflow_parser.py, gardé explicite plutôt que magique.
_RESOLVERS = {
    "GO_VERSION": lambda client: resolve_go_version(client),
    "NODE_VERSION": lambda client: resolve_node_version(client, track="lts"),
}


def resolve_runtime_version(client: RuntimeClient, runtime: RuntimeVar) -> ResolvedVersion:
    """Résout une RuntimeVar selon son nom. Runtimes non pris en charge
    (PYTHON_VERSION, JAVA_VERSION, RUBY_VERSION dans DEFAULT_RUNTIME_VAR_NAMES)
    renvoient UNRESOLVABLE explicite plutôt qu'une erreur — signalés dans le
    rapport comme "non vérifié", jamais silencieusement ignorés.
    """
    resolver = _RESOLVERS.get(runtime.name)
    if resolver is None:
        return ResolvedVersion(
            latest=None,
            source="unresolvable",
            confidence="low",
            note=f"Runtime '{runtime.name}' non pris en charge par gha_audit pour l'instant.",
        )
    return resolver(client)


def resolve_all_runtimes(client: RuntimeClient, runtimes: list[RuntimeVar]) -> dict[str, ResolvedVersion]:
    """Résout une liste de RuntimeVar, dédupliquée par nom de variable."""
    resolved: dict[str, ResolvedVersion] = {}
    for runtime in runtimes:
        if runtime.name in resolved:
            continue
        resolved[runtime.name] = resolve_runtime_version(client, runtime)
    return resolved
