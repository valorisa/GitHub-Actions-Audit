"""Classification des refs `uses: owner/repo[/subpath]@ref`.

Cette classification pilote directement la stratégie de résolution
(voir resolver/action_resolver.py) : chaque RefKind correspond à un
chemin de résolution différent, jamais un seul appel API générique.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from gha_audit.models import RefKind

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SEMVER_MAJOR_RE = re.compile(r"^v\d+$")
_SEMVER_FULL_RE = re.compile(r"^v\d+\.\d+\.\d+([-+][0-9A-Za-z.\-]+)?$")
_FLOATING_NAMES = {"master", "main", "latest", "develop", "next", "edge"}

# Modules hors écosystème "GitHub Actions Marketplace" : le premier segment
# du chemin ressemble à un nom de domaine (contient un point), typiquement
# un module Go importé via `go install`, jamais un `owner/repo` GitHub.
_DOMAIN_LIKE_SEGMENT_RE = re.compile(r"^[a-zA-Z0-9-]+\.[a-zA-Z]{2,}$")


@dataclass(frozen=True)
class ParsedUses:
    """Résultat du parsing d'une chaîne `uses:` ou d'un import Go-like."""

    owner: str
    repo: str
    subpath: str | None
    ref: str
    is_github_action: bool


def parse_uses(raw: str) -> ParsedUses:
    """Parse une chaîne du type `owner/repo/subpath@ref`.

    Fonctionne aussi bien pour des `uses:` de step GitHub Actions que pour
    des chemins d'installation d'outils Go (`honnef.co/go/tools/...@latest`),
    afin que le même point d'entrée serve pour les deux cas du workflow.
    """
    if "@" not in raw:
        raise ValueError(f"Ref sans '@' : {raw!r}")

    path, ref = raw.rsplit("@", 1)
    segments = path.split("/")
    if len(segments) < 2:
        raise ValueError(f"Chemin d'action invalide, 'owner/repo' attendu : {raw!r}")

    owner, repo = segments[0], segments[1]
    subpath = "/".join(segments[2:]) if len(segments) > 2 else None
    is_github_action = not bool(_DOMAIN_LIKE_SEGMENT_RE.match(owner))

    return ParsedUses(owner=owner, repo=repo, subpath=subpath, ref=ref, is_github_action=is_github_action)


def classify_ref(parsed: ParsedUses) -> RefKind:
    """Détermine le RefKind à partir d'une ParsedUses.

    Ordre de priorité volontaire :
    1. Hors écosystème GitHub Releases -> NON_GITHUB_RELEASE, quel que soit le ref.
       (ex: `honnef.co/go/tools/cmd/staticcheck@latest`)
    2. SHA pinné (40 hex) -> PINNED_SHA.
    3. Tag semver complet (vX.Y.Z) -> SEMVER_FULL.
    4. Tag semver majeur seul (vX) -> SEMVER_MAJOR.
    5. Nom de branche flottant connu ou non reconnu -> FLOATING_BRANCH.
    """
    if not parsed.is_github_action:
        return RefKind.NON_GITHUB_RELEASE

    ref = parsed.ref

    if _SHA_RE.match(ref):
        return RefKind.PINNED_SHA

    if _SEMVER_FULL_RE.match(ref):
        return RefKind.SEMVER_FULL

    if _SEMVER_MAJOR_RE.match(ref):
        return RefKind.SEMVER_MAJOR

    # Couvre les noms connus (master/main/latest/...) ET tout ref qui ne
    # matche aucun pattern reconnu : on préfère sur-classifier en FLOATING
    # (le plus prudent) plutôt que de risquer un faux "à jour".
    return RefKind.FLOATING_BRANCH
