# GitHub Actions Audit

Audit and automatic update tool for GitHub Actions workflow versions —
actions and runtimes (Go, Node...), with clear handling of floating refs
(`@master`, `@latest`) and versions outside the GitHub Releases ecosystem.

**Status:** read-only pipeline complete and functional — discovery
(local + remote), parsing, resolution (GitHub API + Go/Node runtimes),
version comparison, and reporting (console/JSON/markdown), all wired
through a CLI (`gha-audit scan`, `scan-org`). 135 tests passing.
YAML rewriting (`--fix`), diff generation, and PR automation
(`writer/`) are not implemented yet — see the roadmap below.

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
├── models.py            # dataclasses partagées (RefKind, ActionUsage, ComparisonResult, ...)
├── config.py             # policy centrale (cache TTL, runtimes surveillés)
├── pipeline.py            # orchestration : discovery → parser → resolver → comparator
├── cli.py                 # CLI (typer) : scan, scan-org, version — aucune décision métier
├── parser/                # extraction pure, aucun I/O réseau
│   ├── ref_classifier.py  # classification owner/repo@ref
│   └── workflow_parser.py # extraction depuis le YAML (ruamel, round-trip)
├── resolver/               # résolution des dernières versions stables
│   ├── cache.py            # cache disque à TTL
│   ├── github_client.py    # wrapper httpx + endpoints API GitHub
│   ├── action_resolver.py  # résolution des actions (releases/tags)
│   └── runtime_resolver.py # résolution Go (go.dev) / Node (nodejs.org, LTS)
├── discovery/               # où trouver les workflows
│   ├── local_discovery.py    # scan filesystem (ex: ~/Projets)
│   └── remote_discovery.py   # scan via API, sans clone
└── audit/                     # comparaison (pure) + policy + rapport
    ├── comparator.py          # compare_versions() pur + classify_*() policy
    └── report.py              # rendu console / JSON / markdown
```

Voir `docs/adr/` pour les décisions de conception documentées.

## Roadmap

En phase de validation sur des scans réels avant d'ouvrir `writer/`
(réécriture YAML, `--fix`, `UpdatePlan`, idempotence) — voir
`CONTRIBUTING.md` pour le principe directeur sur les abstractions.

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

---

## GitHub Actions Audit (FR)

Outil d'audit et de mise à jour automatique des versions dans les
workflows GitHub Actions — actions et runtimes (Go, Node...), avec une
gestion explicite des refs flottantes (`@master`, `@latest`) et des
versions hors écosystème GitHub Releases.

**Statut :** pipeline en lecture seule complet et fonctionnel — découverte
(locale + distante), parsing, résolution (API GitHub + runtimes Go/Node),
comparaison de versions, et rapport (console/JSON/markdown), le tout
exposé via une CLI (`gha-audit scan`, `scan-org`). 135 tests verts.
La réécriture YAML (`--fix`), la génération de diff et l'automatisation
PR (`writer/`) ne sont pas encore implémentées — voir la roadmap plus bas.

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
