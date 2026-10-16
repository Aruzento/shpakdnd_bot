"""One aiogram button factory. Callback, styles and personal ownership pass through."""
from aiogram.types import InlineKeyboardButton
from .registry import get_registry, custom_emoji_enabled
from .renderer import Text

def button(*, key=None, text=None, icon_key=None, **properties):
    registry = get_registry()
    if key is not None:
        text = registry.text(key)
    callback = properties.get("callback_data")
    if callback is not None and not 1 <= len(callback.encode("utf-8")) <= 64:
        raise ValueError("callback_data must contain 1–64 bytes")
    if icon_key is None:
        icon_key = getattr(text, "icon_key", None)
    entry = registry.snapshot().get(icon_key) if icon_key else None
    supported = "icon_custom_emoji_id" in InlineKeyboardButton.model_fields
    if entry:
        original = registry.loader.defaults[icon_key]["fallback"]
        label = str(text)
        show_fallback = getattr(text, "show_fallback", True)
        for prefix in dict.fromkeys((original, entry["fallback"])):
            if prefix and label.startswith(prefix) and (show_fallback or (entry["custom_emoji_id"] and custom_emoji_enabled() and supported)):
                label = label[len(prefix):].lstrip()
                break
        if entry["custom_emoji_id"] and custom_emoji_enabled() and supported and entry["fallback"]:
            properties["icon_custom_emoji_id"] = entry["custom_emoji_id"]
            text = label
        else:
            properties.pop("icon_custom_emoji_id", None)
            text = (entry["fallback"]+" "+label).strip() if show_fallback else label
    elif not custom_emoji_enabled() or not supported:
        properties.pop("icon_custom_emoji_id", None)
    if not text or not str(text).strip():
        raise ValueError("button text must not be empty")
    return InlineKeyboardButton(text=str(text), **properties)
