"""Public story presentation; user-authored text is plain, never executable markup."""

import json
import re
from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup
from app.stories.catalog import discover


def render(session):
    sid = session["id"]
    rows = []
    if session["status"] == "registration":
        stories, _ = discover()
        story = stories.get(session["code"], {})
        text = f"📖 {story.get('title',session['code'])}\n{story.get('description','')}\n\nУчастников: {len(session['participants'])}"
        rows = [
            [Button(text="Участвовать", callback_data=f"story:join:{sid}")],
            [Button(text="Выйти", callback_data=f"story:leave:{sid}")],
        ]
    elif session.get("route_error"):
        text = "📖 " + session["route_error"]
    elif not session.get("node_id"):
        text = f"📖 {session['code']}\nИстория отменена до начала."
    else:
        story = json.loads(session["snapshot_json"])
        node = story["nodes"].get(session["node_id"], {})
        text = re.sub(r"^\s*Сцена\s+\d+[.:]?\s*", "", node.get("text", ""), count=1, flags=re.IGNORECASE)
        if session["status"] == "active":
            tally = session["tally"]
            text += f"\n\nГолосов: {tally['voted']:>{len(str(tally['active']))}}/{tally['active']}"
            text += "\n\nЧто делаем?"
            buttons = []
            for visible, (index, choice) in enumerate(session["choices"], 1):
                text += f"\n{visible}. {choice['text']} — {tally['percent'][choice['id']]:3}%"
                buttons.append(Button(text=str(visible),
                    callback_data=f'story:vote:{sid}:{session["step"]}:{index}'))
            rows.extend(buttons[i:i+3] for i in range(0,len(buttons),3))
        elif session["status"] == "finished":
            text += "\n\nИстория завершена."
        elif session["status"] == "cancelled":
            text += "\n\nИстория отменена."
    if any(len(button.callback_data.encode()) > 64 for row in rows for button in row):
        raise ValueError("Story callback exceeds 64 bytes")
    return text, InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def chunks(text, limit=4000):
    """Lossless plain-text splitting by UTF-16 units, without splitting surrogate pairs."""
    result, part, size = [], [], 0
    for char in text:
        width = len(char.encode("utf-16-le")) // 2
        if size+width > limit:
            result.append("".join(part)); part=[]; size=0
        part.append(char); size+=width
    if part:
        result.append("".join(part))
    return result or [""]
