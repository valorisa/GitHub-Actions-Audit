# 0003 — Séparation comparaison / policy (Comparator / classify)

## Contexte

`audit/comparator.py` doit transformer une version courante et une version
résolue en statut final d'audit (`UP_TO_DATE`, `OUTDATED`, `FLOATING`,
`UNPINNED`, `UNRESOLVABLE`). Une première implémentation mélangeait dans
une seule fonction `compare_action()` : (a) le calcul de comparaison entre
deux versions, et (b) la décision de policy selon le `RefKind` (une
branche flottante est toujours signalée, peu importe la fraîcheur du code
qu'elle pointe ; un SHA pinné n'est jamais considéré obsolète, etc.).

## Décision

Deux couches strictement séparées, avec un type intermédiaire stable :

```text
compare_versions(current, latest) -> ComparisonResult   # fait objectif
classify_action(kind, comparison) -> VersionStatus       # jugement de policy
classify_runtime(comparison)      -> VersionStatus       # jugement de policy
```

`ComparisonResult` (`is_comparable`, `relation: ComparisonRelation`) ne
connaît rien de `RefKind`, de gravité, ou de recommandation — c'est un fait
de comparaison pur (`IDENTICAL` / `OLDER` / `NEWER` / `UNKNOWN`).

`compare_versions()` accepte un paramètre `major_only` : un tag semver
majeur (`v4`) suit déjà automatiquement toute nouvelle version mineure/
patch sur GitHub — le comparer composant par composant à `v4.3.0`
signalerait obsolète en permanence sans raison. Seule l'apparition d'un
nouveau majeur (`v5`) doit compter.

Cette séparation est motivée par deux raisons de changer indépendantes
dans un seul et même appelant (`compare_action`), pas par plusieurs
consommateurs distincts : la définition d'un fait de comparaison ne change
jamais pour les mêmes raisons que la politique d'audit.

## Conséquences

- `ComparisonResult` reste stable même si la policy évolue (ajout de
  `Severity`, de `Recommendation`, de règles de sécurité type CVE/actions
  archivées à l'avenir) — voir section "Non retenu" ci-dessous.
- `classify_action()`/`classify_runtime()` sont testables indépendamment
  de tout appel réseau ou de tout parsing YAML, avec des `ComparisonResult`
  fabriqués à la main dans les tests.
- `AuditItem` porte à la fois `comparison` (le fait) et `status` (le
  jugement), pour que `audit/report.py` (à venir) puisse afficher les deux
  niveaux si besoin.

## Alternatives rejetées

- **"Policy Engine" complet** (profils `conservative`/`security`/
  `enterprise`/`strict`, moteur de règles configurable) : proposé lors
  d'une revue d'architecture externe. Rejeté pour l'instant — aucun besoin
  concret de plusieurs profils n'est encore apparu ; `classify_action()`/
  `classify_runtime()` suffisent et restent une fonction pure facile à
  faire évoluer le jour où un vrai besoin de profils multiples se présente.
- **Couche `Finding` distincte** (`Severity`, `Recommendation` séparés du
  `VersionStatus`) : jugée prématurée pour la V1. Anticipée mais non codée
  — le jour où le projet ajoutera CVE, détection d'actions archivées/
  supprimées, ou score de confiance, `VersionStatus` seul risque de ne
  plus suffire et cette couche redeviendra pertinente.
