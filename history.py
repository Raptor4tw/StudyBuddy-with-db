"""
history.py - Persist and resume StudyBuddy study sessions per user.

Each row in `study_sessions` is one (user, document, topic) session:
the source document text (so it can be re-indexed without re-uploading),
the mastery checklist, the full conversation, and the mistakes list.
"""

import json
import os
import sqlite3
from contextlib import closing

DB_PATH = os.environ.get("USERS_DB_PATH", "studybuddy_users.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def create_session(user_id: int, document_name: str, document_text: str, topic: str,
                    concepts: dict, conversation: list, mistakes: list,
                    current_concept: str | None, current_question: str | None,
                    follow_up_count: int) -> int:
    """Insert a new study session row and return its id."""
    with closing(get_connection()) as conn:
        cur = conn.execute(
            """INSERT INTO study_sessions
               (user_id, document_name, document_text, topic,
                concepts_json, conversation_json, mistakes_json,
                current_concept, current_question, follow_up_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, document_name, document_text, topic,
             json.dumps(concepts), json.dumps(conversation), json.dumps(mistakes),
             current_concept, current_question, follow_up_count),
        )
        conn.commit()
        return cur.lastrowid


def update_session(session_id: int, user_id: int, concepts: dict,
                    conversation: list, mistakes: list,
                    current_concept: str | None, current_question: str | None,
                    follow_up_count: int) -> None:
    """Overwrite the mutable fields of an existing session (called after every turn)."""
    with closing(get_connection()) as conn:
        conn.execute(
            """UPDATE study_sessions
               SET concepts_json = ?, conversation_json = ?, mistakes_json = ?,
                   current_concept = ?, current_question = ?, follow_up_count = ?,
                   updated_at = datetime('now')
               WHERE id = ? AND user_id = ?""",
            (json.dumps(concepts), json.dumps(conversation), json.dumps(mistakes),
             current_concept, current_question, follow_up_count,
             session_id, user_id),
        )
        conn.commit()


def list_sessions(user_id: int) -> list[dict]:
    """Return this user's sessions, most recently updated first."""
    with closing(get_connection()) as conn:
        rows = conn.execute(
            """SELECT id, document_name, topic, updated_at
               FROM study_sessions WHERE user_id = ?
               ORDER BY updated_at DESC""",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def load_session(session_id: int, user_id: int) -> dict | None:
    """Return the full saved state of one session, or None if not found/not owned by this user."""
    with closing(get_connection()) as conn:
        row = conn.execute(
            "SELECT * FROM study_sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()

    if row is None:
        return None

    return {
        "id": row["id"],
        "document_name": row["document_name"],
        "document_text": row["document_text"],
        "topic": row["topic"],
        "concepts": json.loads(row["concepts_json"]),
        "conversation": json.loads(row["conversation_json"]),
        "mistakes": json.loads(row["mistakes_json"]),
        "current_concept": row["current_concept"],  # ← add
        "current_question": row["current_question"],  # ← add
        "follow_up_count": row["follow_up_count"],  # ← add
    }


def delete_session(session_id: int, user_id: int) -> None:
    with closing(get_connection()) as conn:
        conn.execute(
            "DELETE FROM study_sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        )
        conn.commit()