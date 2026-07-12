"""Cache disque simple à TTL pour les réponses de l'API GitHub.

Objectif : éviter de re-frapper l'API à chaque run. C'est critique en
anonyme (60 req/h) et utile même authentifié (5000 req/h) pour un usage
CI répété sur plusieurs workflows/repos dans une même fenêtre de temps.

Le cache est volontairement "best-effort" : une erreur d'écriture disque
(permissions, FS read-only en CI...) ne doit jamais faire échouer le scan.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

# Sentinel dédié : une clé en cache peut légitimement avoir la valeur
# None (ex: réponse 404 mise en cache). `MISSING` permet de distinguer
# "absent/expiré" de "présent avec la valeur None" sans ambiguïté.
MISSING = object()


class DiskCache:
    """Cache clé/valeur JSON sur disque, avec expiration par entrée."""

    def __init__(self, path: str | Path, ttl_seconds: int) -> None:
        self._path = Path(path)
        self._ttl = ttl_seconds
        self._data: dict[str, dict[str, Any]] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def get(self, key: str) -> Any:
        """Renvoie la valeur en cache, ou le sentinel MISSING si absente/expirée.

        Ne renvoie jamais None pour signifier "absent" — None est une
        valeur de cache valide (ex: résultat 404 mémorisé).
        """
        self._load()
        entry = self._data.get(key)
        if entry is None:
            return MISSING
        if time.time() - entry["stored_at"] > self._ttl:
            return MISSING
        return entry["value"]

    def set(self, key: str, value: Any) -> None:
        """Stocke une valeur. Échec d'écriture silencieux (best-effort)."""
        self._load()
        self._data[key] = {"stored_at": time.time(), "value": value}
        try:
            self._path.write_text(json.dumps(self._data), encoding="utf-8")
        except OSError:
            pass
