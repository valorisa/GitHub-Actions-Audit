"""Comparator (pur) + classify() (policy) — séparés explicitement.

Décision d'architecture (voir docs/adr/0003-version-comparison-policy.md) :

    compare_versions()   -> ComparisonResult   (fait objectif, stable dans le temps)
    classify_action() /
    classify_runtime()   -> VersionStatus      (jugement de policy)

compare_versions() ne sait rien de RefKind ni de ce qu'il faut "faire" d'un
écart de version — il répond uniquement à "current et latest représentent-
ils le même état, ou current est-il plus ancien/plus récent ?". Toute
décision (floating = toujours signalé, SHA pinné = toujours ok, tag majeur
comparé différemment d'un pin complet...) vit dans classify_*(), qui est
le seul endroit du module à connaître RefKind.

Cette séparation est motivée par deux raisons de changer indépendantes
dans le même consommateur (compare_action), pas par plusieurs appelants :
le fait de comparaison ne change jamais pour les mêmes raisons que la
politique de classification.
"""

from __future__ import annotations

from gha_audit.models import (
    ActionUsage,
    AuditItem,
    ComparisonRelation,
    ComparisonResult,
    RefKind,
    ResolvedVersion,
    RuntimeVar,
    VersionStatus,
)


def _version_key(value: str) -> tuple[int, ...] | None:
    """Parse une version 'propre' en tuple d'entiers, ou None si non parseable.

    None plutôt qu'une exception : un ref non standard (branche, SHA...)
    ne doit jamais faire planter tout l'audit, juste aboutir à UNKNOWN
    pour cette comparaison précise.
    """
    try:
        parts = value.lstrip("v").split(".")
        return tuple(int(p) for p in parts)
    except (ValueError, AttributeError):
        return None


def compare_versions(current: str, latest: str | None, *, major_only: bool = False) -> ComparisonResult:
    """Compare deux versions et renvoie un fait objectif, sans policy.

    `major_only=True` compare uniquement le premier composant (utilisé
    pour un tag majeur GitHub Actions comme `v4`, qui suit déjà
    automatiquement toute nouvelle version mineure/patch — comparer la
    version complète signalerait obsolète en permanence pour rien).
    """
    if latest is None:
        return ComparisonResult(
            is_comparable=False, relation=ComparisonRelation.UNKNOWN, current=current, latest=latest
        )

    current_key = _version_key(current)
    latest_key = _version_key(latest)
    if current_key is None or latest_key is None:
        return ComparisonResult(
            is_comparable=False, relation=ComparisonRelation.UNKNOWN, current=current, latest=latest
        )

    current_cmp = current_key[0] if major_only else current_key
    latest_cmp = latest_key[0] if major_only else latest_key

    if current_cmp == latest_cmp:
        relation = ComparisonRelation.IDENTICAL
    elif current_cmp < latest_cmp:
        relation = ComparisonRelation.OLDER
    else:
        relation = ComparisonRelation.NEWER

    return ComparisonResult(is_comparable=True, relation=relation, current=current, latest=latest)


def classify_action(kind: RefKind, comparison: ComparisonResult) -> VersionStatus:
    """Applique la policy propre aux actions GitHub, à partir du RefKind
    et du fait de comparaison. Seule fonction du module à connaître RefKind.

    - FLOATING_BRANCH : toujours FLOATING, peu importe `comparison` — le
      problème est la non-reproductibilité, pas la fraîcheur.
    - NON_GITHUB_RELEASE : toujours UNPINNED, `comparison` non pertinent.
    - PINNED_SHA : toujours UP_TO_DATE — un SHA pinné est la pratique la
      plus sûre, jamais un problème à signaler par défaut.
    - SEMVER_MAJOR / SEMVER_FULL : décidé par `comparison.relation`
      (OLDER -> OUTDATED, sinon UP_TO_DATE ; non comparable -> UNRESOLVABLE).
    """
    if kind is RefKind.FLOATING_BRANCH:
        return VersionStatus.FLOATING
    if kind is RefKind.NON_GITHUB_RELEASE:
        return VersionStatus.UNPINNED
    if kind is RefKind.PINNED_SHA:
        return VersionStatus.UP_TO_DATE

    if not comparison.is_comparable:
        return VersionStatus.UNRESOLVABLE
    return VersionStatus.OUTDATED if comparison.relation is ComparisonRelation.OLDER else VersionStatus.UP_TO_DATE


def classify_runtime(comparison: ComparisonResult) -> VersionStatus:
    """Policy pour un runtime : toujours une comparaison de version complète,
    aucune notion de RefKind ni de tag majeur flottant ne s'applique ici."""
    if not comparison.is_comparable:
        return VersionStatus.UNRESOLVABLE
    return VersionStatus.OUTDATED if comparison.relation is ComparisonRelation.OLDER else VersionStatus.UP_TO_DATE


def compare_action(action: ActionUsage, resolved: ResolvedVersion) -> AuditItem:
    """Point d'entrée haut niveau : compare puis classifie une action."""
    major_only = action.kind is RefKind.SEMVER_MAJOR
    comparison = compare_versions(action.ref, resolved.latest, major_only=major_only)
    status = classify_action(action.kind, comparison)

    return AuditItem(
        identifier=action.slug,
        current=action.ref,
        resolved=resolved,
        comparison=comparison,
        status=status,
        file_path=action.file_path,
        category="action",
    )


def compare_runtime(runtime: RuntimeVar, resolved: ResolvedVersion) -> AuditItem:
    """Point d'entrée haut niveau : compare puis classifie un runtime."""
    comparison = compare_versions(runtime.value, resolved.latest, major_only=False)
    status = classify_runtime(comparison)

    return AuditItem(
        identifier=runtime.name,
        current=runtime.value,
        resolved=resolved,
        comparison=comparison,
        status=status,
        file_path=runtime.file_path,
        category="runtime",
    )


def build_audit_items(
    actions: list[ActionUsage],
    action_resolutions: dict[str, ResolvedVersion],
    runtimes: list[RuntimeVar],
    runtime_resolutions: dict[str, ResolvedVersion],
) -> list[AuditItem]:
    """Assemble les listes d'actions/runtimes avec leurs résolutions en
    une liste unique d'AuditItem — l'entrée directe de audit/report.py.

    Une action/runtime sans résolution correspondante (dict incomplet,
    incohérence appelant) est ignorée silencieusement plutôt que de
    planter tout l'audit pour un seul item mal apparié.
    """
    items: list[AuditItem] = []

    for action in actions:
        resolved = action_resolutions.get(action.slug)
        if resolved is not None:
            items.append(compare_action(action, resolved))

    for runtime in runtimes:
        resolved = runtime_resolutions.get(runtime.name)
        if resolved is not None:
            items.append(compare_runtime(runtime, resolved))

    return items
