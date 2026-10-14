"""Named substitutions; plain screens stay plain, HTML values are escaped."""
import html
import logging
from .validation import EditorialError

log = logging.getLogger(__name__)

class Text(str):
    def __new__(cls, value, key="", icon_key=None, *, show_fallback=True):
        obj = super().__new__(cls, value)
        obj.key, obj.icon_key = key, icon_key
        obj.show_fallback = show_fallback
        return obj

def render(key, values, schema, parameters, defaults=None):
    if set(parameters) != set(schema["parameters"]):
        raise EditorialError(f"{key}: caller parameters differ from schema")
    escaped = {k: html.escape(str(v), quote=True) if schema.get("parse_mode") == "HTML" else str(v)
               for k, v in parameters.items()}
    result = values[key].format_map(escaped)
    if len(result.encode("utf-16-le")) // 2 > schema["limit"]:
        log.warning("Editorial rendered value exceeds limit: %s; using default", key)
        result = (defaults or values)[key].format_map(escaped)
    return Text(result, key, schema.get("icon_key"))

def render_icon(entry, *, html_mode=False, enabled=False):
    fallback = entry["fallback"]
    if enabled and html_mode and entry["custom_emoji_id"] and fallback:
        return f'<tg-emoji emoji-id="{entry["custom_emoji_id"]}">{html.escape(fallback)}</tg-emoji>'
    return fallback
