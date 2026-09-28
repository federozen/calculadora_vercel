"""Memoización para cálculos exactos puros (sin Streamlit).

Streamlit vuelve a ejecutar todo el script en cada clic. En el navegador eso hacía
que cada toque tardara varios segundos porque se repetían los mismos solvers con
los mismos datos. Estas funciones son puras: mismo input, mismo output.
"""
from __future__ import annotations

import copy
import functools
from collections.abc import Mapping, Set


def _freeze(value):
    if isinstance(value, Mapping):
        return ("m", tuple(sorted(((repr(k), _freeze(v)) for k, v in value.items()), key=lambda kv: kv[0])))
    if isinstance(value, (list, tuple)):
        return ("l", tuple(_freeze(v) for v in value))
    if isinstance(value, Set):
        return ("s", tuple(sorted(repr(_freeze(v)) for v in value)))
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    return ("r", repr(value))


def memoize(func=None, *, maxsize: int = 256):
    """Cachea por valor de los argumentos y devuelve copias (el caller puede mutarlas)."""
    def decorate(fn):
        cache: dict = {}

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                key = (_freeze(args), _freeze(kwargs))
            except Exception:
                return fn(*args, **kwargs)
            if key in cache:
                return copy.deepcopy(cache[key])
            result = fn(*args, **kwargs)
            if len(cache) >= maxsize:
                cache.clear()
            cache[key] = copy.deepcopy(result)
            return result

        wrapper.cache_clear = cache.clear  # type: ignore[attr-defined]
        return wrapper

    return decorate(func) if func is not None else decorate
