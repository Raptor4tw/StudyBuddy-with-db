"""
history.py - Persist and resume StudyBuddy study sessions per user,
backed by Postgres (Supabase).

Each row in `study_sessions` is one (user, document, topic) session:
the source document text (so it can be re-indexed without re-uploading),
the mastery checklist, the full conversation, and the mistakes list.
"""

import json
import os
from contextlib import closing

import psycopg2
from psycopg2.extras import RealDictCursor

DB_URL = os.environ["SUPABASE_DB_URL"]


def get_connection():
    return psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)


def create_session(user_id: int, document_name: str, document_text: str, topic: str,
                    topics: list, concepts: dict, conversation: list, mistakes: list,
                    current_concept: str | None, current_question: str | None,
                    follow_up_count: int) -> int:
    """Insert a new study session row and return its id."""
    with closing(get_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO study_sessions
                   (user_id, document_name, document_text, topic, topics_json,
                    concepts_json, conversation_json, mistakes_json,
                    current_concept, current_question, follow_up_count)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (user_id, document_name, document_text, topic, json.dumps(topics),
                 json.dumps(concepts), json.dumps(conversation), json.dumps(mistakes),
                 current_concept, current_question, follow_up_count),
            )
            session_id = cur.fetchone()["id"]
        conn.commit()
    return session_id


def update_session(session_id: int, user_id: int, concepts: dict,
                    conversation: list, mistakes: list,
                    current_concept: str | None, current_question: str | None,
                    follow_up_count: int, topic: str | None = None) -> None:
    """Overwrite the mutable fields of an existing session. If `topic` is given,
    also update it — used to turn an upload's placeholder row into a real topic
    session once the user picks one, instead of creating a duplicate row."""
    with closing(get_connection()) as conn:
        with conn.cursor() as cur:
            if topic is not None:
                cur.execute(
                    """UPDATE study_sessions
                       SET topic = %s, concepts_json = %s, conversation_json = %s, mistakes_json = %s,
                           current_concept = %s, current_question = %s, follow_up_count = %s,
                           updated_at = NOW()
                       WHERE id = %s AND user_id = %s""",
                    (topic, json.dumps(concepts), json.dumps(conversation), json.dumps(mistakes),
                     current_concept, current_question, follow_up_count,
                     session_id, user_id),
                )
            else:
                cur.execute(
                    """UPDATE study_sessions
                       SET concepts_json = %s, conversation_json = %s, mistakes_json = %s,
                           current_concept = %s, current_question = %s, follow_up_count = %s,
                           updated_at = NOW()
                       WHERE id = %s AND user_id = %s""",
                    (json.dumps(concepts), json.dumps(conversation), json.dumps(mistakes),
                     current_concept, current_question, follow_up_count,
                     session_id, user_id),
                )
        conn.commit()


def list_sessions(user_id: int) -> list[dict]:
    """Return this user's sessions, most recently updated first."""
    with closing(get_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, document_name, topic, updated_at
                   FROM study_sessions WHERE user_id = %s
                   ORDER BY updated_at DESC""",
                (user_id,),
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def load_session(session_id: int, user_id: int) -> dict | None:
    """Return the full saved state of one session, or None if not found/not owned by this user."""
    with closing(get_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM study_sessions WHERE id = %s AND user_id = %s",
                (session_id, user_id),
            )
            row = cur.fetchone()

    if row is None:
        return None

    return {
        "id": row["id"],
        "document_name": row["document_name"],
        "document_text": row["document_text"],
        "topic": row["topic"],
        "topics": json.loads(row["topics_json"]) if row["topics_json"] else [row["topic"]],
        "concepts": json.loads(row["concepts_json"]),
        "conversation": json.loads(row["conversation_json"]),
        "mistakes": json.loads(row["mistakes_json"]),
        "current_concept": row["current_concept"],
        "current_question": row["current_question"],
        "follow_up_count": row["follow_up_count"],
    }


def delete_session(session_id: int, user_id: int) -> None:
    with closing(get_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM study_sessions WHERE id = %s AND user_id = %s",
                (session_id, user_id),
            )
        conn.commit()

def hide_topic(user_id: int, document_name: str, topic: str) -> None:
    """Remember that this topic was deleted, so extract_topics() results can be
    filtered on future loads of the same document — without this, the topic
    would just reappear since it's regenerated fresh from the document text."""
    with closing(get_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO hidden_topics (user_id, document_name, topic)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (user_id, document_name, topic) DO NOTHING""",
                (user_id, document_name, topic),
            )
        conn.commit()


def get_hidden_topics(user_id: int, document_name: str) -> set:
    with closing(get_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT topic FROM hidden_topics WHERE user_id = %s AND document_name = %s",
                (user_id, document_name),
            )
            rows = cur.fetchall()
    return {r["topic"] for r in rows}

def unhide_topic(user_id: int, document_name: str, topic: str) -> None:
    with closing(get_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM hidden_topics WHERE user_id = %s AND document_name = %s AND topic = %s",
                (user_id, document_name, topic),
            )
        conn.commit()


def get_hidden_topics_list(user_id: int, document_name: str) -> list[str]:
    """Same data as get_hidden_topics() but as an ordered list, for rendering a
    'Hidden topics' section where the person can unhide individual entries."""
    with closing(get_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT topic FROM hidden_topics WHERE user_id = %s AND document_name = %s ORDER BY created_at DESC",
                (user_id, document_name),
            )
            rows = cur.fetchall()
    return [r["topic"] for r in rows]