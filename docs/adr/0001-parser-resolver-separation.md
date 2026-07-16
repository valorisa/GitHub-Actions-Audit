# 0001 — Séparation parser / resolver

## Contexte

L'outil doit extraire des références de version depuis des fichiers YAML
(`uses: owner/repo@ref`, variables `env:`), puis déterminer si ces versions
sont à jour via l'API GitHub. Ces deux opérations pourraient être fusionnées
dans un seul module qui lit un fichier et retourne directement un statut.

## Décision

Le parsing (extraction pure depuis le YAML, `parser/`) et la résolution
(appels réseau vers l'API GitHub, `resolver/`) sont deux modules strictement
séparés, sans dépendance de l'un vers l'autre au-delà des types partagés
dans `models.py`.

`parser/` ne fait aucun I/O réseau. `resolver/` ne sait rien du format YAML.

## Conséquences

- `parser/` est testable sans mock réseau — tests rapides, déterministes.
- `resolver/` est testable avec `httpx.MockTransport` sans jamais lire de
  fichier YAML réel.
- Un changement de format d'entrée (ex: support de GitLab CI YAML) ne
  toucherait que `parser/`. Un changement de source d'API (voir ADR
  rejetée sur `Resolver Protocol`) ne toucherait que `resolver/`.
- Coût : un type intermédiaire (`ActionUsage`, `RuntimeVar` dans
  `models.py`) sert de contrat entre les deux couches.

## Alternatives rejetées

- **Module unique "audit_file(path)"** : plus rapide à écrire au départ,
  mais impossible à tester sans réseau, et impossible à réutiliser pour
  la découverte distante (`discovery/remote_discovery.py`, qui produit du
  contenu YAML sans jamais toucher au disque).
