# GitHub Actions Audit

Audit and automatic update tool for GitHub Actions workflow versions —
actions and runtimes (Go, Node...), with clear handling of floating refs
(`@master`, `@latest`) and versions outside the GitHub Releases ecosystem.

**Status:** early scaffolding (parser, classifier, GitHub API resolver
with cache, local + remote multi-repo discovery). Version comparison,
YAML rewriting, diff generation, and PR automation are not implemented yet.

## Why not just Dependabot?

[Dependabot](https://docs.github.com/en/code-security/dependabot) already
handles version updates for GitHub Actions out of the box — if that's all
you need, use it, it's free and zero-maintenance. `gha-audit` exists for
what Dependabot doesn't cover: flagging floating branch refs as a security
risk (not just "no update available"), auditing runtime versions declared
in workflow `env:` blocks, and detecting tool installs (`go install ...`)
embedded in `run:` steps.

## Architecture

```text
gha_audit/
├── models.py            # dataclasses partagées (RefKind, ActionUsage, ...)
├── config.py             # policy centrale (cache TTL, runtimes surveillés)
├── parser/                # extraction pure, aucun I/O réseau
│   ├── ref_classifier.py  # classification owner/repo@ref
│   └── workflow_parser.py # extraction depuis le YAML (ruamel, round-trip)
├── resolver/               # résolution des dernières versions stables
│   ├── cache.py            # cache disque à TTL
│   └── github_client.py    # wrapper httpx + endpoints API GitHub
└── discovery/               # où trouver les workflows
    ├── local_discovery.py    # scan filesystem (ex: ~/Projets)
    └── remote_discovery.py   # scan via API, sans clone
```

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

---

## gha-audit (FR)

Outil d'audit et de mise à jour automatique des versions dans les
workflows GitHub Actions — actions et runtimes (Go, Node...), avec une
gestion explicite des refs flottantes (`@master`, `@latest`) et des
versions hors écosystème GitHub Releases.

**Statut :** squelette initial (parser, classificateur, résolveur API
GitHub avec cache, découverte multi-repos locale + distante). La
comparaison de versions, la réécriture YAML, la génération de diff et
l'automatisation PR ne sont pas encore implémentées.

## Pourquoi pas juste Dependabot ?

[Dependabot](https://docs.github.com/en/code-security/dependabot) gère
déjà nativement la mise à jour des versions d'actions GitHub — si c'est
tout ce dont tu as besoin, utilise-le, c'est gratuit et sans maintenance.
`gha-audit` comble ce que Dependabot ne couvre pas : signaler les refs
flottantes comme un risque de sécurité (pas juste "pas de mise à jour
disponible"), auditer les versions de runtime déclarées dans les `env:`
des workflows, et détecter les installs d'outils (`go install ...`)
embarqués dans des steps `run:`.

## Développement

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## License

MIT — see [LICENSE](LICENSE).
