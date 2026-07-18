"""Tests du cache disque à TTL."""

import json
import time

from gha_audit.resolver.cache import MISSING, DiskCache


def test_set_then_get_returns_value(tmp_path):
    cache = DiskCache(tmp_path / "cache.json", ttl_seconds=3600)
    cache.set("key1", {"tag_name": "v4.0.0"})
    assert cache.get("key1") == {"tag_name": "v4.0.0"}


def test_get_missing_key_returns_missing_sentinel(tmp_path):
    cache = DiskCache(tmp_path / "cache.json", ttl_seconds=3600)
    assert cache.get("nope") is MISSING


def test_expired_entry_returns_missing_sentinel(tmp_path):
    cache = DiskCache(tmp_path / "cache.json", ttl_seconds=0)
    cache.set("key1", "value")
    time.sleep(0.01)
    assert cache.get("key1") is MISSING


def test_cached_none_value_is_distinguishable_from_missing(tmp_path):
    """Régression : une valeur None en cache (ex. réponse 404 mémorisée)
    ne doit jamais être confondue avec une absence de clé."""
    cache = DiskCache(tmp_path / "cache.json", ttl_seconds=3600)
    cache.set("key1", None)
    assert cache.get("key1") is None
    assert cache.get("key1") is not MISSING


def test_cache_persists_across_instances(tmp_path):
    path = tmp_path / "cache.json"
    cache1 = DiskCache(path, ttl_seconds=3600)
    cache1.set("key1", "value")

    cache2 = DiskCache(path, ttl_seconds=3600)
    assert cache2.get("key1") == "value"


def test_corrupted_cache_file_does_not_crash_returns_missing(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("not valid json {{{", encoding="utf-8")
    cache = DiskCache(path, ttl_seconds=3600)
    assert cache.get("key1") is MISSING


# --- Étape 1 du chantier ETag : CacheEntry (valeur + métadonnées HTTP) ----
#
# get()/set() gardent leur comportement exact (tests ci-dessus, inchangés).
# get_entry()/set_entry() sont additifs — aucun appelant existant ne les
# utilise encore (GitHubClient._get() n'est pas touché avant l'étape 2).


def test_set_entry_then_get_entry_roundtrip_with_etag(tmp_path):
    cache = DiskCache(tmp_path / "cache.json", ttl_seconds=3600)
    cache.set_entry("key1", {"tag_name": "v5.0.0"}, etag='"abc123"', last_modified="Wed, 01 Jul 2026 00:00:00 GMT")

    entry = cache.get_entry("key1")
    assert entry.value == {"tag_name": "v5.0.0"}
    assert entry.etag == '"abc123"'
    assert entry.last_modified == "Wed, 01 Jul 2026 00:00:00 GMT"


def test_set_entry_without_etag_defaults_to_none(tmp_path):
    cache = DiskCache(tmp_path / "cache.json", ttl_seconds=3600)
    cache.set_entry("key1", "value")

    entry = cache.get_entry("key1")
    assert entry.etag is None
    assert entry.last_modified is None


def test_set_via_legacy_api_produces_entry_with_none_etag(tmp_path):
    """set() (l'API historique, toujours utilisée par GitHubClient/
    RuntimeClient) doit produire une entrée lisible via get_entry()."""
    cache = DiskCache(tmp_path / "cache.json", ttl_seconds=3600)
    cache.set("key1", "value")

    entry = cache.get_entry("key1")
    assert entry.value == "value"
    assert entry.etag is None


def test_get_entry_missing_key_returns_missing_sentinel(tmp_path):
    cache = DiskCache(tmp_path / "cache.json", ttl_seconds=3600)
    assert cache.get_entry("nope") is MISSING


def test_get_entry_expired_returns_missing_sentinel(tmp_path):
    cache = DiskCache(tmp_path / "cache.json", ttl_seconds=0)
    cache.set_entry("key1", "value", etag='"abc"')
    time.sleep(0.01)
    assert cache.get_entry("key1") is MISSING


def test_entry_with_etag_persists_across_instances(tmp_path):
    path = tmp_path / "cache.json"
    cache1 = DiskCache(path, ttl_seconds=3600)
    cache1.set_entry("key1", "value", etag='"abc123"')

    cache2 = DiskCache(path, ttl_seconds=3600)
    entry = cache2.get_entry("key1")
    assert entry.value == "value"
    assert entry.etag == '"abc123"'


def test_old_format_cache_file_without_etag_keys_is_still_readable(tmp_path):
    """Compatibilité ascendante : un fichier écrit par une version
    antérieure du cache (sans les clés etag/last_modified) doit rester
    lisible sans erreur — elles redeviennent simplement None."""
    path = tmp_path / "cache.json"
    old_format = {"key1": {"stored_at": time.time(), "value": {"tag_name": "v4.0.0"}}}
    path.write_text(json.dumps(old_format), encoding="utf-8")

    cache = DiskCache(path, ttl_seconds=3600)
    entry = cache.get_entry("key1")

    assert entry.value == {"tag_name": "v4.0.0"}
    assert entry.etag is None
    assert entry.last_modified is None

    # get() (API legacy) fonctionne aussi, sans changement de comportement.
    assert cache.get("key1") == {"tag_name": "v4.0.0"}


def test_get_and_get_entry_agree_on_value(tmp_path):
    cache = DiskCache(tmp_path / "cache.json", ttl_seconds=3600)
    cache.set_entry("key1", {"a": 1}, etag='"xyz"')

    assert cache.get("key1") == cache.get_entry("key1").value
