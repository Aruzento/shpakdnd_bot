"""V1.5 additive feature schemas; no conversion/deletion of legacy fragments."""

from app.mini.mythic.schema import apply as mythic_schema
from app.stories.schema import apply as story_schema


def apply(conn):
    mythic_schema(conn)
    story_schema(conn)
    from app.mini.ui.windows import schema as window_schema
    window_schema(conn)
