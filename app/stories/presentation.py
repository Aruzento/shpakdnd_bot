"""Public story presentation; user-authored text is plain, never executable markup."""

import json
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
            [Button(text="Начать историю (админ)", callback_data=f"story:start:{sid}")],
        ]
    elif not session.get("node_id"):
        text = f"📖 {session['code']}\nИстория отменена до начала."
    else:
        story = json.loads(session["snapshot_json"])
        node = story["nodes"].get(session["node_id"], {})
        text = f"📖 {story['title']}\n\n{node.get('text','')}"
        if session["status"] == "active":
            tally = session["tally"]
            text += f"\n\nГолосов: {tally['voted']}/{tally['active']}"
            for index, choice in enumerate(node.get("choices", [])):
                rows.append(
                    [
                        Button(
                            text=f"{choice['text']} — {tally['percent'][choice['id']]}%",
                            callback_data=f'story:vote:{sid}:{session["step"]}:{index}',
                        )
                    ]
                )
        elif session["status"] == "finished":
            text += "\n\nИстория завершена."
        elif session["status"] == "cancelled":
            text += "\n\nИстория отменена."
    if any(len(button.callback_data.encode()) > 64 for row in rows for button in row):
        raise ValueError("Story callback exceeds 64 bytes")
    return text, InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
