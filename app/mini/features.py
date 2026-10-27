"""Temporary entry gates. Existing recovery/ledgers retain their approved rules."""
import os

UNAVAILABLE = "Режим временно недоступен"

def enabled(mode):
    key = {"duels": "MINI_DUELS_ENABLED", "pvp": "MINI_PVP_ENABLED"}[mode]
    return os.getenv(key, "").lower() in {"1", "true", "yes"}

def require_enabled(mode):
    if not enabled(mode):
        raise ValueError(UNAVAILABLE)
