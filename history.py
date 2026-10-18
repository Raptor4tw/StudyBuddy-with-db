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


def load_document_meta(user_id: int, document_name: str) -> dict | None:
    """Load everything for one document in ONE roundtrip:
      - document_text (from any session)
      - topics snapshot (from the most recent session's topics_json)
      - all sessions with full payloads (so _render_topics_tab and
        _find_session_for don't need extra DB roundtrips)
      - aggregated mistakes across all sessions (deduped by (topic, question))
    """
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

    # document_text/topics_json are identical across sessions of the same doc
    document_text = rows[0]["document_text"]
    topics_snapshot = json.loads(rows[0]["topics_json"]) if rows[0]["topics_json"] else []

    # Aggregate mistakes across all sessions (deduped)
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
        conn.executemany(
            """INSERT INTO llm_usage (user_id, session_id, model, prompt_tokens, completion_tokens)
               VALUES (?, ?, ?, ?, ?)""",
            [(user_id, session_id, u["model"], u["prompt_tokens"], u["completion_tokens"])
             for u in usage],
        )
        conn.commit()


def usage_by_model(user_id: int, session_id: int) -> dict[str, tuple[int, int, int]]:
    """Return {model: (prompt_tokens, completion_tokens, largest single prompt)}
    for one session. The largest prompt shows how close a request came to the
    model's input limit, since each call is sent without chat history."""
    with closing(get_connection()) as conn:
        rows = conn.execute(
            """SELECT model, SUM(prompt_tokens) AS p, SUM(completion_tokens) AS c,
                      MAX(prompt_tokens) AS peak
               FROM llm_usage WHERE user_id = ? AND session_id = ?
               GROUP BY model ORDER BY MIN(id)""",
            (user_id, session_id),
        ).fetchall()
    return {r["model"]: (r["p"], r["c"], r["peak"]) for r in rows}


def get_preferred_model(user_id: int) -> str | None:
    with closing(get_connection()) as conn:
        row = conn.execute(
            "SELECT preferred_model FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return row["preferred_model"] if row else None


def set_preferred_model(user_id: int, model: str) -> None:
    with closing(get_connection()) as conn:
        conn.execute(
            "UPDATE users SET preferred_model = ? WHERE id = ?", (model, user_id)
        )
        conn.commit()