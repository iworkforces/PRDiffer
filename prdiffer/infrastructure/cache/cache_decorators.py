"""Cache decorators for method-level caching with unhashable parameter support.

This module combines cache key utilities and the @cached_method decorator.
"""

import functools
import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, TypeVar, cast

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

__all__ = [
    "CachingMixin",
    "cached_method",
]


# ---------------------------------------------------------------------------
# Cache key utilities
# ---------------------------------------------------------------------------


def _make_hashable(obj: Any, _seen: set[int] | None = None, _depth: int = 0) -> Any:
    """Convert an object to a hashable form recursively with circular reference protection."""
    MAX_DEPTH = 20
    if _depth > MAX_DEPTH:
        return f"<max_depth_exceeded:{type(obj).__name__}>"

    if _seen is None:
        _seen = set()

    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj

    obj_id = id(obj)
    if obj_id in _seen:
        return f"<circular_ref:{type(obj).__name__}>"

    if isinstance(obj, (list, tuple)):
        _seen.add(obj_id)
        try:
            seq = cast("list[Any] | tuple[Any, ...]", obj)
            result: tuple[Any, ...] = tuple(_make_hashable(item, _seen.copy(), _depth + 1) for item in seq)
        finally:
            _seen.discard(obj_id)
        return result
    elif isinstance(obj, dict):
        _seen.add(obj_id)
        try:
            d = cast("dict[Any, Any]", obj)
            pairs: list[tuple[Any, Any]] = [(k, _make_hashable(v, _seen.copy(), _depth + 1)) for k, v in d.items()]
            result = tuple(sorted(pairs))
        finally:
            _seen.discard(obj_id)
        return result
    elif isinstance(obj, set):
        _seen.add(obj_id)
        try:
            s = cast("set[Any]", obj)
            hashable_items: list[Any] = [_make_hashable(item, _seen.copy(), _depth + 1) for item in s]
            try:
                result = tuple(sorted(hashable_items))
            except TypeError as e:
                logger.debug(
                    "Cannot sort hashable items, using string representation",
                    extra={
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "item_count": len(hashable_items),
                    },
                )
                result = tuple(sorted(str(item) for item in hashable_items))
        finally:
            _seen.discard(obj_id)
        return result
    else:
        obj_as_object: object = obj
        obj_type = type(obj_as_object)
        return f"<{obj_type.__name__}:{id(obj_type)}>"


def _generate_cache_key(method_name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """Generate a cache key from method name and arguments."""
    hashable_args = _make_hashable(args)
    hashable_kwargs = _make_hashable(kwargs)

    key_data: dict[str, Any] = {"method": method_name, "args": hashable_args, "kwargs": hashable_kwargs}

    key_json = json.dumps(key_data, sort_keys=True)
    key_hash = hashlib.md5(key_json.encode()).hexdigest()

    return f"{method_name}_{key_hash}"


# ---------------------------------------------------------------------------
# CachingMixin and @cached_method
# ---------------------------------------------------------------------------


class CachingMixin:
    """Mixin class that provides caching capabilities to any class.

    Provides a shared cache dictionary and cache management methods
    used by the @cached_method decorator.

    Thread Safety:
    - All cache operations are protected by a reentrant lock
    - Statistics counters are atomic within locked sections
    """

    def __init__(self, max_cache_size: int = 1000, default_ttl: int = 300) -> None:
        self._cache_lock = threading.RLock()
        self._method_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._cache_hits = 0
        self._cache_misses = 0
        self._max_cache_size = max_cache_size
        self._default_ttl = default_ttl

    def _evict_expired_entries(self) -> None:
        """Remove expired cache entries."""
        with self._cache_lock:
            current_time = time.time()
            expired_keys = [key for key, entry in self._method_cache.items() if current_time > entry.get("expires_at", float("inf"))]
            for key in expired_keys:
                del self._method_cache[key]

    def _enforce_size_limit(self) -> None:
        """Enforce cache size limit using LRU eviction."""
        with self._cache_lock:
            while len(self._method_cache) > self._max_cache_size:
                self._method_cache.popitem(last=False)

    def clear_cache(self) -> None:
        """Clear all cached method results."""
        with self._cache_lock:
            self._method_cache.clear()
            self._cache_hits = 0
            self._cache_misses = 0

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics.

        Returns:
            Dict containing cache size, hit rate, and other statistics
        """
        with self._cache_lock:
            total_requests = self._cache_hits + self._cache_misses
            hit_rate = self._cache_hits / total_requests if total_requests else 0.0

            return {
                "size": len(self._method_cache),
                "hits": self._cache_hits,
                "misses": self._cache_misses,
                "hit_rate": hit_rate,
                "total_requests": total_requests,
                "max_size": self._max_cache_size,
                "default_ttl": self._default_ttl,
            }


def cached_method(ttl: int | None = None, key_prefix: str | None = None) -> Callable[[F], F]:
    """Decorator for caching method results with support for unhashable parameters.

    Applied to methods of classes that inherit from CachingMixin.
    Handles unhashable parameters by converting them to hashable forms.

    Args:
        ttl: Time-to-live for cache entries in seconds
        key_prefix: Optional prefix for cache keys

    Returns:
        Decorated method with caching capability
    """

    def decorator(method: F) -> F:
        @functools.wraps(method)
        def wrapper(self: object, *args: Any, **kwargs: Any) -> Any:
            assert isinstance(self, CachingMixin), (
                f"@cached_method can only be used on methods of classes "
                f"that inherit from CachingMixin. {self.__class__.__name__} "
                f"does not inherit from CachingMixin."
            )

            method_name = getattr(method, "__name__", "unknown")
            if key_prefix:
                method_name = f"{key_prefix}_{method_name}"

            cache_key = _generate_cache_key(method_name, args, kwargs)

            with self._cache_lock:
                if cache_key in self._method_cache:
                    entry = self._method_cache[cache_key]
                    current_time = time.time()
                    if current_time <= entry.get("expires_at", float("inf")):
                        self._method_cache.move_to_end(cache_key)
                        self._cache_hits += 1
                        return entry["value"]
                    else:
                        del self._method_cache[cache_key]

            if (self._cache_hits + self._cache_misses) % 10 == 0:
                self._evict_expired_entries()

            with self._cache_lock:
                self._cache_misses += 1

            result = method(self, *args, **kwargs)

            entry_ttl = ttl if ttl is not None else self._default_ttl
            expires_at = time.time() + entry_ttl if entry_ttl > 0 else float("inf")

            with self._cache_lock:
                self._method_cache[cache_key] = {
                    "value": result,
                    "expires_at": expires_at,
                    "created_at": time.time(),
                }

            self._enforce_size_limit()

            return result

        def clear_method_cache(self: CachingMixin) -> None:
            """Clear cache entries for this specific method."""
            method_name = getattr(method, "__name__", "unknown")
            if key_prefix:
                method_name = f"{key_prefix}_{method_name}"

            with self._cache_lock:
                keys_to_remove = [key for key in list(self._method_cache.keys()) if key.startswith(f"{method_name}_")]
                for key in keys_to_remove:
                    del self._method_cache[key]

        setattr(wrapper, "clear_cache", clear_method_cache)

        return cast(F, wrapper)

    return decorator
