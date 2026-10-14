"""Bounded metadata cache, immutable snapshots and all-files atomic reload."""
import logging
import threading
from pathlib import Path
from types import MappingProxyType
from .validation import EditorialError, read_json, validate_entry

log = logging.getLogger(__name__)
DEFAULTS = Path(__file__).parent / "defaults" / "ru"
SCHEMA = Path(__file__).parent / "schema.json"

def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(freeze(v) for v in value)
    return value

class Loader:
    def __init__(self, directory=None, *, defaults=DEFAULTS, schema=SCHEMA):
        self.directory = Path(directory).resolve() if directory is not None else None
        # External edits must never make a checkout dirty or trigger its watcher.
        if self.directory is not None:
            for parent in (self.directory, *self.directory.parents):
                if (parent / ".git").exists():
                    raise EditorialError(f"{self.directory}: external directory must be outside Git checkout")
        self.schemas = read_json(Path(schema))
        self.defaults = {}
        self.files = {}
        for file in sorted(Path(defaults).glob("*.json")):
            entries = read_json(file)
            self.files[file.name] = set(entries)
            for key, value in entries.items():
                if key in self.defaults or key not in self.schemas:
                    raise EditorialError(f"{file}: {key}: duplicate/unknown default")
                validate_entry(file, key, value, self.schemas[key])
                self.defaults[key] = value
        if set(self.defaults) != set(self.schemas):
            raise EditorialError("defaults/schema keys differ")
        for key, value in self.defaults.items():
            if self.schemas[key]["kind"] == "glossary" and self.schemas.get(value["icon_key"], {}).get("kind") != "icon":
                raise EditorialError(f"glossary.json: {key}: unknown default icon_key")
        self.defaults_snapshot = freeze(self.defaults)
        self._snapshot = self.defaults_snapshot
        self._lock = threading.RLock()
        self._signature = None
        self.generation = 0
        self.refresh()

    @property
    def snapshot(self):
        return self._snapshot

    def _stat(self):
        if self.directory is None:
            return ()
        result = []
        for file in sorted(self.directory.glob("*.json")):
            stat = file.stat()
            result.append((file.name, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino))
        return tuple(result)

    def refresh(self):
        with self._lock:
            try:
                signature = self._stat()
                if signature == self._signature:
                    return self._snapshot
                values = dict(self.defaults)
                for filename, *_ in signature:
                    file = self.directory / filename
                    if filename not in self.files:
                        raise EditorialError(f"{file}: <root>: unknown catalog")
                    for key, value in read_json(file).items():
                        if key not in self.files[filename]:
                            raise EditorialError(f"{file}: {key}: unknown key or wrong catalog")
                        schema = self.schemas[key]
                        # Objects can override individual fields; explicit nulls still fail.
                        if schema["kind"] in {"icon", "glossary"} and isinstance(value, dict):
                            value = {**values[key], **value}
                        validate_entry(file, key, value, schema)
                        values[key] = value
                for key, value in values.items():
                    if self.schemas[key]["kind"] == "glossary" and self.schemas.get(value["icon_key"], {}).get("kind") != "icon":
                        raise EditorialError(f"glossary.json: {key}: unknown icon_key")
                # A concurrent write must not publish a mixed read.
                if signature != self._stat():
                    return self._snapshot
                candidate = freeze(values)
                self._snapshot = candidate
                self._signature = signature
                self.generation += 1
            except (EditorialError, OSError) as error:
                # Remember a failed revision, so unchanged bad files log only once.
                self._signature = locals().get("signature", self._signature)
                log.warning("Editorial revision rejected; retaining last valid snapshot: %s", error)
            return self._snapshot
