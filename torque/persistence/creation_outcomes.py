"""Exact persisted target lookup for interrupted creation outcomes."""


def load_created_agent(db, agent_id: str) -> dict | None:
    cursor = db._conn.execute(
        "SELECT id, name, kind, cell_type FROM agents WHERE id = ?",
        (agent_id,),
    )
    row = cursor.fetchone()
    return dict(zip((column[0] for column in cursor.description), row)) if row else None
