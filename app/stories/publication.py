"""Durable publication claims. Never retry an uncertain send automatically."""

from app.config import DB_PATH
from app.stories.repository import transaction


def claim(session_id, step, db_path=DB_PATH):
    with transaction(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM story_publications WHERE session_id=? AND step=?",
            (session_id, step),
        ).fetchone()
        if row is None or row["status"] != "pending":
            return None
        conn.execute(
            "UPDATE story_publications SET status='sending' WHERE session_id=? AND step=?",
            (session_id, step),
        )
        return dict(row)


def finish(session_id, step, message_id=None, db_path=DB_PATH):
    with transaction(db_path) as conn:
        conn.execute(
            "UPDATE story_publications SET status=?,message_id=? WHERE session_id=? AND step=? AND status='sending'",
            (
                "sent" if message_id is not None else "uncertain",
                message_id,
                session_id,
                step,
            ),
        )


def recover(db_path=DB_PATH):
    with transaction(db_path) as conn:
        conn.execute(
            "UPDATE story_publications SET status='uncertain' WHERE status='sending'"
        )
