# 0002 — Classification des refs (RefKind)

## Contexte

Une ref `uses: owner/repo@ref` peut être un SHA pinné, un tag semver majeur
(`v4`), un tag semver complet (`v0.10.0`), une branche flottante (`master`,
`latest`), ou pointer vers un module hors écosystème GitHub Releases (un
import Go via `go install owner.tld/path@ref`). Chacun de ces cas appelle
une stratégie de résolution et une politique d'audit différentes.

## Décision

Un enum unique `RefKind` à 5 valeurs (`PINNED_SHA`, `SEMVER_MAJOR`,
`SEMVER_FULL`, `FLOATING_BRANCH`, `NON_GITHUB_RELEASE`), calculé une fois
par `parser/ref_classifier.py::classify_ref()` et porté par `ActionUsage`.

En cas d'ambiguïté (ref inconnue, ni SHA ni semver reconnu), la
classification retombe sur `FLOATING_BRANCH` plutôt que d'inventer une
nouvelle catégorie — c'est le choix le plus prudent (sur-classifier en
"à surveiller" plutôt que risquer un faux "à jour").

## Conséquences

- `resolver/action_resolver.py` choisit sa stratégie (appel API, ou aucun
  appel pour `PINNED_SHA`/`NON_GITHUB_RELEASE`) uniquement à partir de ce
  `RefKind`.
- `audit/comparator.py::classify_action()` est la seule fonction à
  connaître `RefKind` pour la policy d'audit (voir ADR 0003).

## Alternatives rejetées

- **Trois enums séparés** (`ReferenceKind` / `VersionScheme` /
  `ResolutionStrategy`) : proposé lors d'une revue d'architecture externe,
  dans la crainte que `RefKind` devienne un "God Enum". Rejeté : à 5
  valeurs couvrant exactement les cas rencontrés dans la suite de tests,
  cette séparation anticipe un besoin (CalVer, DateVer, autres schémas de
  version) qui n'est pas encore apparu dans le projet. Revisitable dès
  qu'un cas réel ne rentre plus dans le modèle actuel.
