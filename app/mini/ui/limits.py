"""Telegram message/caption budgets count UTF-16 units, not Python characters."""


def units(text):
    return len(str(text).encode("utf-16-le")) // 2


def clip(text, limit, suffix="…"):
    text = str(text)
    if units(text) <= limit:
        return text
    budget = max(0, limit - units(suffix))
    return (
        text.encode("utf-16-le")[: budget * 2].decode("utf-16-le", errors="ignore")
        + suffix
    )
