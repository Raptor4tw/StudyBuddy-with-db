"""
history.py - Persist and resume StudyBuddy study sessions per user,
backed by Postgres (Supabase).

Each row in `study_sessions` is one (user, document, topic) session:
the source document text (so it can be re-indexed without re-uploading),
the mastery checklist, the full conversation, and the mistakes list.
"""

from __future__ import annotations

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


def list_documents(user_id: int) -> list[dict]:
    """Return distinct documents for this user (one row per document_name),
    most recently updated first."""
    with closing(get_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT document_name, MAX(updated_at) AS last_updated
                   FROM study_sessions
                   WHERE user_id = %s
                   GROUP BY document_name
                   ORDER BY last_updated DESC""",
                (user_id,),
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def _is_broken_topic_name(s: str) -> bool:
    """Same filter as study_agent._is_broken_topic — kept here too so old
    rows in the DB never leak broken fragments into the UI."""
    if not s:
        return True
    low = s.lower()
    if "is_lecture_material" in low:
        return True
    if low in {"topics", "topic", "json", "true", "false", "null"}:
        return True
    if any(ch in s for ch in "{}[]"):
        return True
    if s[0] in "\"':" or s[-1] in "\"':":
        return True
    if len(s) < 3 or len(s) > 80:
        return True
    return False


def load_document_meta(user_id: int, document_name: str) -> dict | None:
    """Load everything for one document in ONE roundtrip."""
    with closing(get_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, document_text, topic, topics_json,
                          concepts_json, conversation_json, mistakes_json,
                          current_concept, current_question, follow_up_count,
                          updated_at
                   FROM study_sessions
                   WHERE user_id = %s AND document_name = %s
                   ORDER BY updated_at DESC""",
                (user_id, document_name),
            )
            rows = cur.fetchall()

    if not rows:
        return None

    document_text = rows[0]["document_text"]
    raw_topics = json.loads(rows[0]["topics_json"]) if rows[0]["topics_json"] else []
    # Filter broken entries left over from older runs
    topics_snapshot = [
        str(t).strip() for t in (raw_topics or [])
        if isinstance(t, str) and not _is_broken_topic_name(str(t).strip())
    ]

    merged_mistakes: list = []
    seen_keys: set = set()

    sessions = []
    for r in rows:
        try:
            concepts = json.loads(r["concepts_json"]) if r["concepts_json"] else {}
        except (TypeError, ValueError):
            concepts = {}
        try:
            conversation = json.loads(r["conversation_json"]) if r["conversation_json"] else []
        except (TypeError, ValueError):
            conversation = []
        try:
            mistakes = json.loads(r["mistakes_json"]) if r["mistakes_json"] else []
        except (TypeError, ValueError):
            mistakes = []

        for m in mistakes:
            key = (m.get("topic"), m.get("question"))
            if key not in seen_keys:
                seen_keys.add(key)
                merged_mistakes.append(m)

        sessions.append({
            "id": r["id"],
            "topic": r["topic"],
            "concepts": concepts,
            "conversation": conversation,
            "mistakes": mistakes,
            "current_concept": r["current_concept"],
            "current_question": r["current_question"],
            "follow_up_count": r["follow_up_count"],
            "updated_at": r["updated_at"],
        })

    return {
        "document_name": document_name,
        "document_text": document_text,
        "topics": topics_snapshot,
        "sessions": sessions,
        "mistakes": merged_mistakes,
    }


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


def delete_document(user_id: int, document_name: str) -> None:
    """Delete all sessions AND hidden_topics entries for one document."""
    with closing(get_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM study_sessions WHERE user_id = %s AND document_name = %s",
                (user_id, document_name),
            )
            cur.execute(
                "DELETE FROM hidden_topics WHERE user_id = %s AND document_name = %s",
                (user_id, document_name),
            )
        conn.commit()


def hide_topic(user_id: int, document_name: str, topic: str) -> None:
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
    with closing(get_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT topic FROM hidden_topics WHERE user_id = %s AND document_name = %s ORDER BY created_at DESC",
                (user_id, document_name),
            )
            rows = cur.fetchall()
    return [r["topic"] for r in rows]


def record_usage(user_id: int, session_id: int | None, usage: list[dict]) -> None:
    """Store LLM calls ({"model", "prompt_tokens", "completion_tokens"}) for a user.
    session_id is None for calls made before a topic is chosen (topic extraction)."""
    if not usage:
        return
    with closing(get_connection()) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO llm_usage
                   (user_id, session_id, model, prompt_tokens, completion_tokens)
                   VALUES (%s, %s, %s, %s, %s)""",
                [(user_id, session_id, u["model"],
                  u["prompt_tokens"], u["completion_tokens"])
                 for u in usage],
            )
        conn.commit()


def usage_by_model(user_id: int, session_id: int) -> dict[str, tuple[int, int, int]]:
    """Return {model: (prompt_tokens, completion_tokens, largest single prompt)}
    for one session. The largest prompt shows how close a request came to the
    model's input limit, since each call is sent without chat history."""
    with closing(get_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT model,
                          SUM(prompt_tokens)     AS p,
                          SUM(completion_tokens) AS c,
                          MAX(prompt_tokens)     AS peak
                   FROM llm_usage
                   WHERE user_id = %s AND session_id = %s
                   GROUP BY model
                   ORDER BY MIN(id)""",
                (user_id, session_id),
            )
            rows = cur.fetchall()
    return {
        r["model"]: (int(r["p"] or 0), int(r["c"] or 0), int(r["peak"] or 0))
        for r in rows
    }


def get_preferred_model(user_id: int) -> str | None:
    with closing(get_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT preferred_model FROM users WHERE id = %s",
                (user_id,),
            )
            row = cur.fetchone()
    if row is None:
        return None
    return row["preferred_model"]


def set_preferred_model(user_id: int, model: str) -> None:
    with closing(get_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET preferred_model = %s WHERE id = %s",
                (model, user_id),
            )
        conn.commit()