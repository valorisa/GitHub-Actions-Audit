"""Découverte de workflows sur le filesystem local.

Utilisé par `gha-audit scan <dossier> --recursive` pour parcourir un ou
plusieurs clones locaux (typiquement ~/Projets) sans toucher au réseau.
"""

from __future__ import annotations

from pathlib import Path

from gha_audit.discovery.base import WorkflowSource

_WORKFLOW_SUFFIXES = (".yml", ".yaml")


def find_workflow_files(root: str | Path) -> list[Path]:
    """Trouve tous les fichiers sous `<repo>/.github/workflows/*.yml(.yaml)`.

    `root` peut être : un seul repo, ou un dossier parent contenant
    plusieurs repos (ex: ~/Projets) — on cherche récursivement tout
    dossier `.github/workflows/` sous `root`, à n'importe quelle profondeur,
    donc un seul appel couvre aussi bien un repo isolé qu'un portefeuille
    complet de repos clonés côte à côte.
    """
    root = Path(root)
    if not root.exists():
        return []

    results: list[Path] = []
    for workflows_dir in root.rglob(".github/workflows"):
        if not workflows_dir.is_dir():
            continue
        for suffix in _WORKFLOW_SUFFIXES:
            results.extend(sorted(workflows_dir.glob(f"*{suffix}")))
    return sorted(set(results))


def load_local_sources(root: str | Path) -> list[WorkflowSource]:
    """Charge en mémoire chaque workflow trouvé sous `root`.

    `repo_slug` est dérivé du nom du dossier parent du `.github/` détecté,
    en best-effort — c'est purement informatif pour l'affichage du rapport,
    jamais utilisé pour résoudre des versions.
    """
    sources: list[WorkflowSource] = []
    for path in find_workflow_files(root):
        content = path.read_text(encoding="utf-8")
        repo_dir = _find_repo_root(path)
        sources.append(
            WorkflowSource(
                display_name=str(path),
                content=content,
                local_path=path,
                repo_slug=repo_dir.name if repo_dir else None,
            )
        )
    return sources


def _find_repo_root(workflow_path: Path) -> Path | None:
    """Remonte l'arborescence jusqu'au dossier qui contient `.github/`."""
    for parent in workflow_path.parents:
        if (parent / ".github").is_dir():
            return parent
    return None
