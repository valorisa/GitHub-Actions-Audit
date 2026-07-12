"""Tests du cache disque à TTL."""

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
