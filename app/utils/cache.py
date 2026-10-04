import time

_cache = {}


def get_cache(key):
    item = _cache.get(key)
    if not item:
        return None
    expires_at, value = item
    if expires_at is not None and expires_at < time.time():
        _cache.pop(key, None)
        return None
    return value


def set_cache(key, value, ttl_seconds=None):
    expires_at = None
    if ttl_seconds:
        expires_at = time.time() + ttl_seconds
    _cache[key] = (expires_at, value)


def clear_cache_prefix(prefix):
    """Hapus semua cache yang key-nya diawali `prefix` (dipakai setelah approve/reject
    agar dashboard tidak menampilkan antrian lama sampai TTL habis)."""
    for k in [k for k in list(_cache.keys()) if str(k).startswith(prefix)]:
        _cache.pop(k, None)
