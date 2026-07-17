"""Client HTTP vers l'API GitHub, spécialisé pour les besoins de gha_audit.

Stratégie d'authentification, dans cet ordre (couvre local ET CI sans
configuration supplémentaire) :
1. Token explicite (`--token` CLI).
2. Variable d'environnement `GITHUB_TOKEN` (cas CI standard : GitHub
   Actions l'injecte automatiquement, `env: GITHUB_TOKEN: secrets.GITHUB_TOKEN`).
3. `gh auth token` en subprocess (cas local : réutilise l'auth `gh` déjà
   en place dans ton workflow habituel, sans dupliquer un token à gérer).
4. Anonyme (60 req/h). L'outil reste fonctionnel mais le cache devient
   critique ; un appelant (CLI) doit avertir explicitement l'utilisateur.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

import httpx

from gha_audit.resolver.cache import MISSING, DiskCache

GITHUB_API_BASE = "https://api.github.com"


class RateLimitExceeded(RuntimeError):
    """Levée quand l'API GitHub répond 403 avec le rate limit épuisé.

    Volontairement une exception dédiée (pas une HTTPStatusError générique)
    pour que la CLI puisse afficher un message actionnable plutôt qu'une
    stacktrace httpx brute.
    """


def resolve_github_token(explicit: str | None = None) -> str | None:
    """Résout le token GitHub à utiliser, selon la stratégie décrite plus haut."""
    if explicit:
        return explicit

    env_token = os.environ.get("GITHUB_TOKEN")
    if env_token:
        return env_token

    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    token = result.stdout.strip()
    if result.returncode == 0 and token:
        return token
    return None


class GitHubClient:
    """Wrapper httpx minimal : deux endpoints, cache, gestion 403/404."""

    def __init__(
        self,
        cache: DiskCache,
        token: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        self._cache = cache
        # `transport` injectable : c'est ce qui permet de tester ce client
        # avec httpx.MockTransport, sans jamais dépendre du réseau réel.
        self._client = httpx.Client(
            base_url=GITHUB_API_BASE,
            headers=headers,
            transport=transport,
            timeout=10.0,
            # Un repo renommé/déplacé renvoie un 301 vers sa nouvelle URL
            # canonique — c'est un cas normal (ex: azure/trusted-signing-action
            # rencontré en usage réel), pas une erreur à faire remonter.
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _get(self, path: str) -> Any | None:
        cached = self._cache.get(path)
        if cached is not MISSING:
            return cached

        response = self._client.get(path)

        if response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
            raise RateLimitExceeded(
                f"Rate limit GitHub épuisé pour {path}. "
                "Authentifie-toi via --token, GITHUB_TOKEN, ou `gh auth login`."
            )

        if response.status_code == 404:
            # Absence de release/tag = résultat valide, pas une erreur.
            # Mis en cache aussi, pour ne pas re-frapper l'API pour rien.
            self._cache.set(path, None)
            return None

        response.raise_for_status()
        data = response.json()
        self._cache.set(path, data)
        return data

    def get_latest_release(self, owner: str, repo: str) -> dict | None:
        """Chemin de résolution privilégié : releases GitHub formelles."""
        return self._get(f"/repos/{owner}/{repo}/releases/latest")

    def get_tags(self, owner: str, repo: str, per_page: int = 30) -> list[dict]:
        """Fallback pour les actions qui ne publient que des tags Git,
        sans Release GitHub formelle (ex: zaproxy/action-baseline)."""
        result = self._get(f"/repos/{owner}/{repo}/tags?per_page={per_page}")
        return result or []

    def list_repos_for_owner(self, owner: str, per_page: int = 100) -> list[dict]:
        """Liste tous les repos d'un user/org, avec pagination.

        Utilise `/users/{owner}/repos` (fonctionne aussi pour la plupart
        des cas d'usage courants ; pour une org GitHub au sens strict avec
        des repos privés non listés via /users, préférer /orgs/{owner}/repos
        — non couvert ici volontairement pour rester simple par défaut).
        """
        repos: list[dict] = []
        page = 1
        while True:
            batch = self._get(f"/users/{owner}/repos?per_page={per_page}&page={page}&type=owner")
            if not batch:
                break
            repos.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
        return repos

    def list_workflow_files(self, owner: str, repo: str) -> list[dict]:
        """Liste le contenu de `.github/workflows/` d'un repo via l'API Contents.

        Renvoie une liste vide (pas une erreur) si le repo n'a pas de
        dossier workflows — cas fréquent et parfaitement normal.
        """
        result = self._get(f"/repos/{owner}/{repo}/contents/.github/workflows")
        if not result:
            return []
        return [
            entry
            for entry in result
            if entry.get("type") == "file" and entry.get("name", "").endswith((".yml", ".yaml"))
        ]

    def get_file_content(self, owner: str, repo: str, path: str) -> str | None:
        """Récupère et décode le contenu (base64) d'un fichier via l'API Contents."""
        import base64

        result = self._get(f"/repos/{owner}/{repo}/contents/{path}")
        if not result or "content" not in result:
            return None
        return base64.b64decode(result["content"]).decode("utf-8")
