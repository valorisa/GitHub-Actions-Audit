"""Découverte de workflows via l'API GitHub, sans clone local.

Utilisé par `gha-audit scan-org --owner <user>` : liste tous les repos
d'un compte, récupère le contenu de leurs workflows via l'API Contents.

Volontairement en lecture seule : les WorkflowSource produites ici ont
`local_path=None`, donc `writer/yaml_updater.py` refusera d'y toucher.
Réécrire sans clone impliquerait de committer via l'API Git (blob/tree/
commit/ref) — hors scope pour l'instant, voir architecture.
"""

from __future__ import annotations

from gha_audit.discovery.base import WorkflowSource
from gha_audit.resolver.github_client import GitHubClient


def load_remote_sources(
    client: GitHubClient,
    owner: str,
    include_forks: bool = False,
    include_archived: bool = False,
) -> list[WorkflowSource]:
    """Parcourt tous les repos d'un owner et charge leurs workflows en mémoire.

    Chaque appel réseau passe par le cache du GitHubClient — un deuxième
    `scan-org` dans la fenêtre de TTL ne re-télécharge rien.
    """
    sources: list[WorkflowSource] = []

    for repo in client.list_repos_for_owner(owner):
        if repo.get("fork") and not include_forks:
            continue
        if repo.get("archived") and not include_archived:
            continue

        repo_name = repo["name"]
        slug = f"{owner}/{repo_name}"

        for entry in client.list_workflow_files(owner, repo_name):
            content = client.get_file_content(owner, repo_name, entry["path"])
            if content is None:
                continue
            sources.append(
                WorkflowSource(
                    display_name=f"{slug}:{entry['path']}",
                    content=content,
                    local_path=None,  # lecture seule, volontairement
                    repo_slug=slug,
                )
            )

    return sources
