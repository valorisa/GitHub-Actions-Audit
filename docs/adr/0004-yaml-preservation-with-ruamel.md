# 0004 — Préservation du YAML avec ruamel.yaml

## Contexte

`gha-audit` doit pouvoir réécrire un fichier de workflow (`--fix`, à venir)
en ne modifiant que les refs d'action et les variables runtime obsolètes,
sans jamais altérer le reste : commentaires, ordre des clés, style de
citation, ancres YAML éventuelles. Un fichier de workflow GitHub Actions
est un document qu'un humain relit et édite manuellement — un dump complet
reformaté après une modification automatique serait une régression
inacceptable (diff illisible, perte de commentaires).

## Décision

`ruamel.yaml` en mode round-trip (`typ="rt"`, `preserve_quotes=True`,
`width=4096` pour éviter le retour à la ligne automatique) pour tout
chargement et toute réécriture de workflow. `PyYAML` est explicitement
écarté : il ne préserve ni les commentaires, ni l'ordre, ni le style, et
reformate systématiquement le document entier à l'écriture.

Chaque `ActionUsage`/`RuntimeVar` porte un `YamlPath` (tuple de clés/index
depuis la racine) permettant de cibler le nœud exact à modifier dans
l'arbre `ruamel` déjà chargé, sans jamais re-parser ni dumper l'intégralité
du document.

## Conséquences

- `writer/yaml_updater.py` (à venir) doit réutiliser la même instance
  `YAML()` — la même configuration — entre lecture et écriture, sinon le
  round-trip perd sa garantie de préservation.
- Le diff généré après un `--fix` doit être vérifié automatiquement
  (garde-fou) : si `ruamel` touche autre chose que les lignes ciblées par
  les `YamlPath` prévus, c'est un signal d'anomalie, pas juste un détail
  cosmétique.
- Coût : `ruamel.yaml` a une extension C optionnelle (`ruamel.yaml.clib`)
  sans wheel précompilé pour certaines plateformes (ex: Termux/Android
  arm64), ce qui peut déclencher une tentative de compilation locale à
  l'installation. La bibliothèque reste fonctionnelle en pur Python sans
  cette extension.

## Alternatives rejetées

- **PyYAML** : plus répandu, mais ne préserve ni commentaires ni style —
  disqualifiant pour l'objectif de réécriture non destructive.
- **Manipulation texte par regex** (remplacer directement la sous-chaîne
  `owner/repo@ref` dans le texte brut) : évite la dépendance à un parseur
  YAML complet, mais fragile face aux cas limites (refs dans des chaînes
  commentées, indentation non standard, ancres/alias). Écarté au profit
  d'un arbre structuré avec positions explicites (`YamlPath`).
