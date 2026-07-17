"""Modèles de données partagés par tout le pipeline gha_audit.

Convention du projet : dataclasses stdlib, pas pydantic (cohérent avec
stormgrill / prompt-grill-framework).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RefKind(str, Enum):
    """Classification d'une ref `uses: owner/repo@ref`.

    Pilote directement la stratégie de résolution (voir resolver/).
    """

    PINNED_SHA = "pinned_sha"           # 40 hex chars, jamais "obsolète" par défaut
    SEMVER_MAJOR = "semver_major"       # v4
    SEMVER_FULL = "semver_full"         # v0.10.0
    FLOATING_BRANCH = "floating_branch"  # master, main, latest
    NON_GITHUB_RELEASE = "non_github_release"  # module hors écosystème GitHub Releases


class VersionStatus(str, Enum):
    """Statut final attribué à un item audité (action ou runtime).

    C'est un jugement de POLICY (voir audit/comparator.py::classify_action /
    classify_runtime), pas un fait de comparaison brut — pour ça, voir
    ComparisonResult/ComparisonRelation ci-dessous.
    """

    UP_TO_DATE = "up_to_date"
    OUTDATED = "outdated"
    FLOATING = "floating"
    UNPINNED = "unpinned"
    UNRESOLVABLE = "unresolvable"


class ComparisonRelation(str, Enum):
    """Fait objectif de comparaison entre deux versions — indépendant de
    toute notion de policy (RefKind, gravité, recommandation...).

    Valeur stable dans le temps : contrairement à VersionStatus, elle ne
    change pas si la politique de classification évolue.
    """

    IDENTICAL = "identical"
    OLDER = "older"     # current < latest
    NEWER = "newer"     # current > latest (rare : cache en retard, etc.)
    UNKNOWN = "unknown"  # non comparable (ref non semver, latest absent...)


@dataclass(frozen=True)
class ComparisonResult:
    """Résultat pur de la comparaison current vs latest — aucune notion de
    RefKind, de gravité ou de recommandation ici. Produit par
    audit/comparator.py::compare_versions(), consommé par classify_*()."""

    is_comparable: bool
    relation: ComparisonRelation
    current: str | None
    latest: str | None


@dataclass(frozen=True)
class YamlPath:
    """Chemin d'accès à un nœud dans l'arbre ruamel, pour réécriture ciblée.

    Suite de clés/index depuis la racine du document. Ex: ("jobs", "build",
    "steps", 2, "uses") pointe vers le `uses:` du 3e step du job "build".
    """

    parts: tuple[str | int, ...]


@dataclass
class ActionUsage:
    """Une occurrence de `uses: owner/repo[/subpath]@ref` dans un workflow."""

    file_path: str
    owner: str
    repo: str
    subpath: str | None       # ex. "upload-sarif" pour github/codeql-action/upload-sarif
    ref: str                  # ex. "v4", "master", "a1b2c3d..."
    raw: str                  # chaîne brute complète, ex. "actions/checkout@v4"
    yaml_path: YamlPath
    kind: RefKind = field(default=RefKind.FLOATING_BRANCH)

    @property
    def slug(self) -> str:
        """owner/repo, sans le subpath ni la ref — identifiant du dépôt GitHub."""
        return f"{self.owner}/{self.repo}"


@dataclass
class RuntimeVar:
    """Une variable de version runtime (GO_VERSION, NODE_VERSION, ...)."""

    file_path: str
    name: str
    value: str
    yaml_path: YamlPath


@dataclass
class ResolvedVersion:
    """Résultat de la résolution de la dernière version stable connue."""

    latest: str | None
    source: str                # "releases_latest" | "tags_sorted" | "go_dl" | "node_dist" | "unresolvable"
    confidence: str            # "high" | "medium" | "low"
    note: str = ""


@dataclass(frozen=True)
class ParseError:
    """Une source dont le contenu YAML n'a pas pu être parsé.

    Un fait, pas une décision : le pipeline (voir pipeline.py::extract_all)
    isole la source en échec et continue sur les autres plutôt que de
    faire échouer tout le scan pour un seul fichier malformé — un YAML
    ambigu (ex: un `name:` avec un ':' non échappé) est un cas réel
    rencontré en usage, pas une situation hypothétique.
    """

    source: str
    message: str


@dataclass
class AuditItem:
    """Une ligne du rapport d'audit — action ou runtime, unifiées."""

    identifier: str            # "actions/checkout" ou "GO_VERSION"
    current: str
    resolved: ResolvedVersion
    comparison: ComparisonResult  # fait objectif brut, avant policy
    status: VersionStatus         # jugement de policy final (voir classify_*)
    file_path: str
    category: str               # "action" | "runtime"
