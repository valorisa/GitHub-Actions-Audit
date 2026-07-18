"""Configuration centrale de gha_audit : cache, runtimes surveillés, policy.

Tout ce qui relève d'un choix de politique (quels runtimes auditer, durée
de vie du cache...) vit ici plutôt qu'en dur dans les modules métier, pour
rester injectable/testable et modifiable sans toucher au code de résolution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CACHE_PATH = Path(".gha_audit_cache.json")
DEFAULT_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6h : assez court pour rester frais,
# assez long pour qu'un `scan` puis un `fix` dans la même session ne
# déclenchent pas deux fois les mêmes appels API.

DEFAULT_RUNTIME_VAR_NAMES = frozenset(
    {"GO_VERSION", "NODE_VERSION", "PYTHON_VERSION", "JAVA_VERSION", "RUBY_VERSION"}
)


@dataclass
class GhaAuditConfig:
    """Config résolue une fois par run, passée explicitement aux modules
    qui en ont besoin (pas de globale implicite)."""

    cache_path: Path = DEFAULT_CACHE_PATH
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS
    github_token: str | None = None
    known_runtime_vars: frozenset[str] = field(default_factory=lambda: DEFAULT_RUNTIME_VAR_NAMES)
    check_sha_age: bool = False  # cf. architecture: un SHA pinné n'est jamais
    # "obsolète" par défaut ; ce flag active un audit de fraîcheur optionnel.
    max_concurrent_requests: int = 8  # borne de parallélisation des résolutions
    # d'actions (voir resolver/action_resolver.py::DEFAULT_MAX_WORKERS) —
    # levier de performance le plus rentable mesuré en usage réel.
    node_track: str = "lts"  # "lts" | "current" — piste de comparaison pour
    # NODE_VERSION. Par défaut LTS : une variable comme NODE_VERSION: '18'
    # est presque toujours un choix volontaire de LTS, pas un simple retard
    # de mise à jour vers la dernière version "current".
