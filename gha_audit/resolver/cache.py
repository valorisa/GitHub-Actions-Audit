"""Cache disque simple à TTL pour les réponses de l'API GitHub.

Objectif : éviter de re-frapper l'API à chaque run. C'est critique en
anonyme (60 req/h) et utile même authentifié (5000 req/h) pour un usage
CI répété sur plusieurs workflows/repos dans une même fenêtre de temps.

Le cache est volontairement "best-effort" : une erreur d'écriture disque
(permissions, FS read-only en CI...) ne doit jamais faire échouer le scan.

Étape 1 du chantier ETag : CacheEntry porte les métadonnées HTTP
nécessaires aux requêtes conditionnelles (etag, last_modified), en plus
de la valeur et du TTL classique. Étape 2 (GitHubClient._get) exploite
get_entry()/get_stale_entry()/set_entry() pour le GET conditionnel.

Thread-safety : ajoutée en prévision de la parallélisation bornée des
résolutions (plusieurs threads appelant le même DiskCache simultanément).
Toutes les opérations qui touchent `self._data` ou le fichier sur disque
sont protégées par un verrou unique — un cache est un point de
contention partagé, pas la peine d'optimiser plus finement pour l'instant.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Sentinel dédié : une clé en cache peut légitimement avoir la valeur
# None (ex: réponse 404 mise en cache). `MISSING` permet de distinguer
# "absent/expiré" de "présent avec la valeur None" sans ambiguïté.
MISSING = object()


@dataclass(frozen=True)
class CacheEntry:
    """Une entrée de cache complète : valeur + métadonnées de fraîcheur.

    Compatibilité ascendante : une entrée sérialisée par une version
    antérieure du cache (sans `etag`/`last_modified` dans le JSON) reste
    lisible — ces champs sont simplement absents et redeviennent `None`
    ici, sans erreur ni migration nécessaire.
    """

    value: Any
    stored_at: float
    etag: str | None = None
    last_modified: str | None = None


class DiskCache:
    """Cache clé/valeur JSON sur disque, avec expiration par entrée.

    Thread-safe : toutes les méthodes publiques acquièrent un verrou
    interne unique avant de lire/écrire `self._data` ou le fichier.
    """

    def __init__(self, path: str | Path, ttl_seconds: int) -> None:
        self._path = Path(path)
        self._ttl = ttl_seconds
        self._data: dict[str, dict[str, Any]] = {}
        self._loaded = False
        self._lock = threading.Lock()

    def _load(self) -> None:
        # Appelée uniquement sous verrou (voir méthodes publiques ci-dessous).
        if self._loaded:
            return
        self._loaded = True
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def _persist(self) -> None:
        # Appelée uniquement sous verrou.
        try:
            self._path.write_text(json.dumps(self._data), encoding="utf-8")
        except OSError:
            pass

    def get_entry(self, key: str) -> CacheEntry | object:
        """Renvoie l'entrée complète (valeur + métadonnées), ou MISSING.

        `raw.get("etag")`/`raw.get("last_modified")` plutôt que `raw["etag"]` :
        c'est ce qui rend une entrée écrite par l'ancien format (sans ces
        clés) lisible sans erreur — elles redeviennent simplement None.
        """
        with self._lock:
            self._load()
            raw = self._data.get(key)
            if raw is None:
                return MISSING
            if time.time() - raw["stored_at"] > self._ttl:
                return MISSING
            return CacheEntry(
                value=raw["value"],
                stored_at=raw["stored_at"],
                etag=raw.get("etag"),
                last_modified=raw.get("last_modified"),
            )

    def get_stale_entry(self, key: str) -> CacheEntry | object:
        """Renvoie l'entrée telle quelle sur disque, en ignorant le TTL.

        Réservée au flux de requête conditionnelle (voir GitHubClient._get) :
        quand get_entry() a déjà signalé MISSING (TTL expiré), ceci permet
        de récupérer un éventuel ETag connu pour tenter un GET conditionnel
        plutôt qu'un GET inconditionnel complet. Ne remplace pas get_entry()
        — deux responsabilités distinctes : l'une applique la politique de
        fraîcheur, l'autre donne un accès brut au contenu du cache.
        """
        with self._lock:
            self._load()
            raw = self._data.get(key)
            if raw is None:
                return MISSING
            return CacheEntry(
                value=raw["value"],
                stored_at=raw["stored_at"],
                etag=raw.get("etag"),
                last_modified=raw.get("last_modified"),
            )

    def set_entry(
        self,
        key: str,
        value: Any,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> None:
        """Stocke une valeur avec ses métadonnées de fraîcheur éventuelles.

        Échec d'écriture silencieux (best-effort) — cohérent avec set().
        """
        with self._lock:
            self._load()
            self._data[key] = {
                "stored_at": time.time(),
                "value": value,
                "etag": etag,
                "last_modified": last_modified,
            }
            self._persist()

    def get(self, key: str) -> Any:
        """Renvoie la valeur en cache, ou le sentinel MISSING si absente/expirée.

        Ne renvoie jamais None pour signifier "absent" — None est une
        valeur de cache valide (ex: résultat 404 mémorisé).
        """
        entry = self.get_entry(key)
        if entry is MISSING:
            return MISSING
        return entry.value

    def set(self, key: str, value: Any) -> None:
        """Stocke une valeur sans métadonnées HTTP (comportement historique,
        inchangé pour tous les appelants actuels de GitHubClient/RuntimeClient)."""
        self.set_entry(key, value)
