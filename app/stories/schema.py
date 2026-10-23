"""Story tables are separate from Mini economies and Events."""


def apply(conn):
    statements = [
        """CREATE TABLE IF NOT EXISTS story_sessions (
      id INTEGER PRIMARY KEY,chat_id INTEGER NOT NULL,thread_id INTEGER NOT NULL DEFAULT 0,
      code TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'registration' CHECK(status IN ('registration','active','finished','cancelled')),
      snapshot_json TEXT NOT NULL DEFAULT '{}',node_id TEXT,step INTEGER NOT NULL DEFAULT 0,
      creator_id INTEGER NOT NULL,created_at INTEGER NOT NULL,result_json TEXT NOT NULL DEFAULT '{}')""",
        "CREATE UNIQUE INDEX IF NOT EXISTS story_one_active ON story_sessions(chat_id,thread_id) WHERE status IN ('registration','active')",
        """CREATE TABLE IF NOT EXISTS story_participants (
      session_id INTEGER NOT NULL REFERENCES story_sessions(id) ON DELETE CASCADE,user_id INTEGER NOT NULL,
      mode TEXT NOT NULL CHECK(mode IN ('dnd','mini')),entity_json TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,
      PRIMARY KEY(session_id,user_id))""",
        """CREATE TABLE IF NOT EXISTS story_votes (
      session_id INTEGER NOT NULL REFERENCES story_sessions(id),step INTEGER NOT NULL,user_id INTEGER NOT NULL,
      choice_id TEXT NOT NULL,PRIMARY KEY(session_id,step,user_id),
      FOREIGN KEY(session_id,user_id) REFERENCES story_participants(session_id,user_id))""",
        """CREATE TABLE IF NOT EXISTS story_steps (
      session_id INTEGER NOT NULL REFERENCES story_sessions(id),step INTEGER NOT NULL,node_id TEXT NOT NULL,
      choice_id TEXT,next_node TEXT,result_json TEXT NOT NULL,PRIMARY KEY(session_id,step))""",
        """CREATE TABLE IF NOT EXISTS story_publications (
      session_id INTEGER NOT NULL REFERENCES story_sessions(id),step INTEGER NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sending','sent','uncertain')),
      message_id INTEGER,PRIMARY KEY(session_id,step))""",
        """CREATE TABLE IF NOT EXISTS story_operations (
      operation_key TEXT PRIMARY KEY,payload TEXT NOT NULL,result_json TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS story_enrollment (
      chat_id INTEGER NOT NULL,thread_id INTEGER NOT NULL,user_id INTEGER NOT NULL,
      session_id INTEGER NOT NULL REFERENCES story_sessions(id),mode TEXT NOT NULL,stage TEXT NOT NULL,
      data_json TEXT NOT NULL DEFAULT '{}',PRIMARY KEY(chat_id,thread_id,user_id))""",
    ]
    for statement in statements:
        conn.execute(statement)
