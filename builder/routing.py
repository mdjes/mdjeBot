"""Decorators that register handler methods for callback routes and conversation states."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def route(*names: str) -> Callable:
    """Mark a method as the handler for callback data ``<name>[:arg...]`` (name is the first two parts)."""

    def decorator(fn: Callable) -> Callable:
        fn._routes = names
        return fn

    return decorator


def on_state(*names: str) -> Callable:
    """Mark a method as the handler for plain messages while the user is in conversation state ``name``."""

    def decorator(fn: Callable) -> Callable:
        fn._states = names
        return fn

    return decorator


def collect(obj: Any) -> tuple[dict[str, Callable], dict[str, Callable]]:
    routes: dict[str, Callable] = {}
    states: dict[str, Callable] = {}
    for attr in dir(type(obj)):
        fn = getattr(obj, attr, None)
        if not callable(fn):
            continue
        for name in getattr(fn, "_routes", ()):
            routes[name] = fn
        for name in getattr(fn, "_states", ()):
            states[name] = fn
    return routes, states
