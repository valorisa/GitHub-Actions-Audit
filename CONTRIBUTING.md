# Contribuer à gha-audit

## Principe directeur sur les abstractions

Toute nouvelle abstraction (interface, protocol, couche supplémentaire,
enum scindé) doit être justifiée par l'un de ces deux critères — pas par
anticipation d'un besoin hypothétique :

1. **Critère structurel** — une interface/protocol ne se justifie qu'à
   partir de **deux implémentations réelles**, pas anticipées. Exemple :
   un `Resolver Protocol` pour supporter GitHub + GitLab ne se justifie
   que le jour où GitLab est réellement implémenté, pas avant (voir ADR
   0002 pour la discussion complète).

2. **Critère de séparation des responsabilités** — une fonction se scinde
   dès qu'elle mélange deux raisons de changer indépendantes, **même avec
   un seul appelant**. Exemple : `audit/comparator.py` sépare le fait de
   comparaison (`ComparisonResult`, stable) de la décision de policy
   (`VersionStatus`, qui peut évoluer) — pas parce que deux consommateurs
   en avaient besoin, mais parce que ces deux logiques ne changent jamais
   pour les mêmes raisons (voir ADR 0003).

En cas de doute : privilégier l'implémentation la plus simple qui marche
aujourd'hui, documenter le compromis dans un ADR si la décision est
notable, et laisser la seconde occurrence réelle (pas hypothétique)
justifier l'abstraction.

## Ce qu'on évite pour l'instant

Architecture hexagonale, injection de dépendances généralisée, système de
plugins, Event Bus, CQRS — aucun n'est justifié par un besoin observé dans
le projet à ce stade. Voir `docs/adr/` pour les décisions déjà actées et
les alternatives explicitement rejetées.

## Conventions

- Dataclasses stdlib, pas de pydantic.
- Tests avec `httpx.MockTransport` — jamais d'appel réseau réel dans la
  suite de tests.
- `pytest tests/ -v` doit rester vert avant tout commit.
