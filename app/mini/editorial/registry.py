"""Live compatibility mappings and pinned snapshots at render boundaries."""
import asyncio
import contextvars
import functools
import inspect
import os
import threading
from collections.abc import Mapping
from contextlib import contextmanager
from .loader import Loader
from .renderer import render, render_icon

_current = contextvars.ContextVar("mini_editorial_snapshot", default=None)
_registry = None
_registry_lock = threading.Lock()

class Registry:
    def __init__(self, directory=None, **kwargs):
        self.loader = Loader(directory, **kwargs)
    def snapshot(self):
        pinned = _current.get()
        if pinned is not None and pinned[0] is self:
            return pinned[1]
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return self.loader.refresh()
        # Event-loop callers use middleware/background refresh; no disk IO here.
        return self.loader.snapshot
    @contextmanager
    def read(self, snapshot=None):
        token = _current.set((self, self.snapshot() if snapshot is None else snapshot))
        try:
            yield
        finally:
            _current.reset(token)
    def text(self, key, **parameters):
        return render(key, self.snapshot(), self.loader.schemas[key], parameters, self.loader.defaults)
    def glossary(self, key, variant="full", *, decorated=False):
        entry = self.snapshot()[key]
        label = entry[variant]
        return f"{self.icon(entry['icon_key'])} {label}".strip() if decorated else label
    def icon(self, key, *, html_mode=False):
        return render_icon(self.snapshot()[key], html_mode=html_mode, enabled=custom_emoji_enabled())

def custom_emoji_enabled():
    return os.getenv("MINI_CUSTOM_EMOJI_ENABLED", "").lower() in {"1", "true", "yes"}

def get_registry():
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = Registry(os.getenv("MINI_EDITORIAL_DIR") or "/var/lib/shpakdnd/editorial/ru")
    return _registry

def text(key, **parameters):
    return get_registry().text(key, **parameters)

def glossary(key, variant="full", *, decorated=False):
    return get_registry().glossary(key, variant, decorated=decorated)

def icon(key, *, html_mode=False):
    return get_registry().icon(key, html_mode=html_mode)

def screen(function):
    """Every screen uses one snapshot, even across concurrent updates."""
    if inspect.iscoroutinefunction(function):
        @functools.wraps(function)
        async def asynchronous(*args, **kwargs):
            registry = get_registry()
            pinned = _current.get()
            if pinned is not None and pinned[0] is registry:
                return await function(*args, **kwargs)
            snapshot = await asyncio.to_thread(registry.loader.refresh)
            with registry.read(snapshot):
                return await function(*args, **kwargs)
        return asynchronous
    @functools.wraps(function)
    def synchronous(*args, **kwargs):
        registry = get_registry()
        with registry.read():
            return function(*args, **kwargs)
    return synchronous

class LiveMapping(Mapping):
    def __init__(self, entries, *, terms=False, variant="full", decorated=False):
        self.entries, self.terms, self.variant, self.decorated = entries, terms, variant, decorated
    def __iter__(self):
        return iter(self.entries)
    def __len__(self):
        return len(self.entries)
    def __getitem__(self, key):
        target = self.entries[key]
        if isinstance(target, tuple):
            return tuple(text(k) for k in target)
        if self.terms:
            return glossary(target, self.variant, decorated=self.decorated)
        return text(target)

class LiveSequence:
    def __init__(self, entries):
        self.entries = entries
    def __len__(self):
        return len(self.entries)
    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(text(k) for k in self.entries[index])
        return text(self.entries[index])
    def __iter__(self):
        return (text(k) for k in self.entries)
