"""Small cache-backed throttles shared by every code-sending endpoint."""

from django.conf import settings
from django.core.cache import cache


def client_ip(request):
    """The client address, honouring TRUSTED_PROXY_COUNT proxies in front."""
    remote = request.META.get("REMOTE_ADDR", "unknown")
    proxies = getattr(settings, "TRUSTED_PROXY_COUNT", 0)

    if proxies:
        parts = [p.strip() for p in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if p.strip()]
        if len(parts) >= proxies:
            return parts[-proxies]

    return remote


def hit(key, limit, window_seconds):
    """Count one event under ``key``; return False once ``limit`` is exceeded.

    Uses add + incr so the count is atomic on a shared cache such as Redis.
    """
    cache.add(key, 0, window_seconds)

    try:
        count = cache.incr(key)
    except ValueError:  # key expired between add and incr
        cache.set(key, 1, window_seconds)
        count = 1

    return count <= limit
