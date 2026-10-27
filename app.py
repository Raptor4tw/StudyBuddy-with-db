import os
from datetime import timedelta

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

load_dotenv()

from agent.models import load_model_efforts, load_model_options
from agent.study_agent import QUIZ_KINDS, StudyBuddyAgent, quiz_done_from_conversation
from tools.document_loader import load_document
from tools.document_validator import check_is_learning_material
from tools.rag import RAGIndex
from auth import init_db
from auth_ui import require_login, logout, render_stay_signed_in_help
import history

try:
    for _key, _value in st.secrets.items():
        os.environ.setdefault(_key, str(_value))
except Exception:
    pass

MODEL_OPTIONS = load_model_options()
MODEL_EFFORTS = load_model_efforts()
CONTEXT_WARN_SHARE = float(os.getenv("CONTEXT_WARN_SHARE", "0.4"))
USAGE_BLOCK_HOURS = float(os.getenv("USAGE_BLOCK_HOURS", "5"))

st.set_page_config(page_title="StudyBuddy", page_icon="📚", layout="wide")

st.markdown("""
<style>
    section[data-testid="stSidebar"] {
        min-width: 300px !important;
    }
    section[data-testid="stSidebar"] button {
        text-align: left;
        justify-content: flex-start;
    }
    section[data-testid="stSidebar"] .stButton > button {
        font-size: 0.9rem;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    div[data-testid="stChatMessage"] {
        border-radius: 10px;
    }
    .sb-user-badge {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 6px;
        min-width: 0;
    }
    .sb-avatar {
        width: 32px;
        height: 32px;
        border-radius: 50%;
        background: linear-gradient(135deg, #4f46e5, #7c3aed);
        color: white;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 600;
        font-size: 13px;
        flex-shrink: 0;
    }
    .sb-user-meta {
        min-width: 0;
        font-size: 0.85rem;
    }
    .sb-username {
        font-weight: 600;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .sb-useremail {
        color: #8a8a8a;
        font-size: 0.75rem;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    section[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] {
        align-items: center;
        gap: 4px;
    }
</style>
""", unsafe_allow_html=True)

init_db()

PENDING_TOPIC_LABEL = "Uploaded – choose a topic"

for _k, _v in {
    "agent": None,
    "topics": [],
    "conversation": [],
    "app_state": "no_document",
    "active_document": None,
    "active_topic": None,
    "last_filename": None,
    "celebrate": False,
    "mistakes_by_user": {},
    "document_text": None,
    "current_session_id": None,
    "pending_topic": None,
    "_uploader_had_file": False,
    "_uploader_key": 0,
    "session_is_pending": False,
    "_pending_dialog": None,
    "_rag_cache": {},
    "model": os.getenv("MODEL"),
    "usage_saved": 0,
    # Reasoning effort per model, starting at each model's own default; always sent
    "efforts_by_model": {m: default for m, (_, default) in MODEL_EFFORTS.items()},
    "last_eval": None,
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ── Cached DB queries ────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def _docs_cached(user_id: int) -> list[dict]:
    return history.list_documents(user_id)


@st.cache_data(ttl=300, show_spinner=False)
def _doc_meta_cached(user_id: int, doc_name: str) -> dict | None:
    return history.load_document_meta(user_id, doc_name)


@st.cache_data(ttl=60, show_spinner=False)
def _usage_cached(user_id: int, session_id: int) -> dict:
    return history.usage_by_model(user_id, session_id)


@st.cache_data(ttl=30, show_spinner=False)
def _block_usage_cached(user_id: int) -> dict:
    return history.usage_in_current_block(user_id, USAGE_BLOCK_HOURS)


@st.cache_resource(show_spinner=False)
def _build_rag_cached(user_id: int, doc_name: str, document_text: str) -> RAGIndex:
    return RAGIndex(document_text)


# ── Cache-aware accessors ────────────────────────────────────────────────────

def _get_rag(doc_name: str, document_text: str) -> RAGIndex:
    user = st.session_state.get("user")
    if not user:
        raise RuntimeError("No user in session — cannot build RAG index.")
    cache = st.session_state._rag_cache
    if doc_name in cache:
        return cache[doc_name]
    with st.spinner("Loading document model… (first time only)"):
        rag = _build_rag_cached(user["id"], doc_name, document_text)
    cache[doc_name] = rag
    return rag


def _invalidate_docs_cache():
    _docs_cached.clear()
    st.session_state._docs_cache = None


def _invalidate_doc_meta(doc_name: str | None = None):
    _doc_meta_cached.clear()


def _invalidate_rag(doc_name: str | None = None):
    if doc_name is None:
        st.session_state._rag_cache.clear()
    else:
        st.session_state._rag_cache.pop(doc_name, None)
    try:
        _build_rag_cached.clear()
    except Exception:
        pass


def _get_doc_meta(doc_name: str) -> dict | None:
    user = st.session_state.get("user")
    if not user:
        return None
    return _doc_meta_cached(user["id"], doc_name)


def _get_docs() -> list[dict]:
    user = st.session_state.get("user")
    if not user:
        return []
    return _docs_cached(user["id"])


def _get_mistakes(doc: str | None = None) -> list:
    user = st.session_state.get("user")
    if not user:
        return []
    doc = doc or st.session_state.active_document
    if not doc:
        return []
    return st.session_state.mistakes_by_user.get(user["id"], {}).get(doc, [])


def _set_mistakes(mistakes: list, doc: str | None = None) -> None:
    user = st.session_state.get("user")
    if not user:
        return
    doc = doc or st.session_state.active_document
    if not doc:
        return
    st.session_state.mistakes_by_user.setdefault(user["id"], {})[doc] = mistakes


def _save_usage():
    user = st.session_state.get("user")
    agent = st.session_state.agent
    if not user or not agent or not hasattr(agent, "usage"):
        return
    try:
        history.record_usage(
            user["id"],
            st.session_state.current_session_id,
            agent.usage[st.session_state.usage_saved:],
        )
        st.session_state.usage_saved = len(agent.usage)
        _usage_cached.clear()
        _block_usage_cached.clear()
    except Exception:
        pass


def _save_preferred_model():
    user = st.session_state.get("user")
    if not user:
        return
    try:
        history.set_preferred_model(user["id"], st.session_state.model)
    except Exception:
        pass


def _persist():
    user = st.session_state.get("user")
    if not user:
        return
    if not st.session_state.current_session_id or not st.session_state.agent:
        return
    agent = st.session_state.agent
    history.update_session(
        st.session_state.current_session_id,
        user["id"],
        agent.concepts,
        st.session_state.conversation,
        _get_mistakes(),
        agent.current_concept,
        agent.current_question,
        agent.follow_up_count,
    )
    _invalidate_doc_meta(st.session_state.active_document)


def _find_session_in_meta(meta: dict | None, topic: str) -> dict | None:
    if not meta:
        return None
    for s in meta["sessions"]:
        if s["topic"] == topic:
            return s
    return None


def _find_placeholder_in_meta(meta: dict | None) -> dict | None:
    return _find_session_in_meta(meta, PENDING_TOPIC_LABEL)


def _open_document(document_name: str) -> None:
    user = st.session_state.get("user")
    if not user:
        return
    meta = _get_doc_meta(document_name)
    if not meta:
        st.error("Document not found.")
        return

    st.session_state.active_document = document_name
    st.session_state.last_filename = document_name
    st.session_state.document_text = meta["document_text"]

    hidden = history.get_hidden_topics(user["id"], document_name)
    topics_snapshot = meta["topics"] or []
    st.session_state.topics = [
        t for t in topics_snapshot
        if t not in hidden and t and "is_lecture_material" not in t.lower()
    ]

    if not _get_mistakes(document_name):
        _set_mistakes(list(meta["mistakes"]), document_name)

    st.session_state.agent = None
    st.session_state.conversation = []
    st.session_state.current_session_id = None
    st.session_state.active_topic = None
    st.session_state.app_state = "document_open"
    st.session_state.session_is_pending = False


def _close_document() -> None:
    st.session_state.active_document = None
    st.session_state.last_filename = None
    st.session_state.document_text = None
    st.session_state.topics = []
    st.session_state.agent = None
    st.session_state.conversation = []
    st.session_state.current_session_id = None
    st.session_state.active_topic = None
    st.session_state.app_state = "no_document"
    st.session_state.session_is_pending = False


# ── Dialog infrastructure ────────────────────────────────────────────────────

def _close_dialog() -> None:
    st.session_state["_pending_dialog"] = None
    st.rerun()


@st.dialog("Log out?")
def _dlg_logout():
    st.write("Bist du sicher, dass du dich abmelden möchtest?")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Cancel", use_container_width=True, key="logout_cancel"):
            _close_dialog()
    with col2:
        if st.button("Log out", type="primary", use_container_width=True, key="logout_confirm"):
            _save_usage()
            st.session_state["_pending_dialog"] = None
            logout()


@st.dialog("Delete document?")
def _dlg_delete_document(name: str):
    st.write(
        f"Willst du **{name}** wirklich löschen? Alle Topics, Sessions "
        "und Fehler-Reviews dieses Dokuments werden entfernt. Das kann "
        "nicht rückgängig gemacht werden."
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Cancel", use_container_width=True, key="del_doc_cancel"):
            _close_dialog()
    with col2:
        if st.button("Delete", type="primary", use_container_width=True, key="del_doc_confirm"):
            user = st.session_state["user"]
            history.delete_document(user["id"], name)
            st.session_state.mistakes_by_user.get(user["id"], {}).pop(name, None)
            _invalidate_rag(name)
            _invalidate_doc_meta(name)
            _invalidate_docs_cache()
            if st.session_state.active_document == name:
                _close_document()
            st.session_state["_pending_dialog"] = None
            st.rerun()


@st.dialog("Hide topic?")
def _dlg_hide_topic(topic: str):
    st.write(
        f"Willst du das Topic **{topic}** ausblenden? Die zugehörige Session "
        "und alle dazu gespeicherten Fehler werden gelöscht. Du kannst es "
        "später im **Hidden**-Tab wiederherstellen."
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Cancel", use_container_width=True, key="hide_topic_cancel"):
            _close_dialog()
    with col2:
        if st.button("Hide", type="primary", use_container_width=True, key="hide_topic_confirm"):
            user = st.session_state["user"]
            doc = st.session_state.active_document
            meta = _get_doc_meta(doc)
            session = _find_session_in_meta(meta, topic)
            if session:
                history.delete_session(session["id"], user["id"])
            history.hide_topic(user["id"], doc, topic)
            if topic in st.session_state.topics:
                st.session_state.topics.remove(topic)
            _set_mistakes([m for m in _get_mistakes() if m.get("topic") != topic])
            if st.session_state.active_topic == topic:
                st.session_state.agent = None
                st.session_state.conversation = []
                st.session_state.current_session_id = None
                st.session_state.active_topic = None
                st.session_state.app_state = "document_open"
            _invalidate_doc_meta(doc)
            _invalidate_docs_cache()
            st.session_state["_pending_dialog"] = None
            st.rerun()


@st.dialog("Delete mistake?")
def _dlg_delete_mistake(index: int):
    st.write("Diesen Eintrag aus der Review-Liste entfernen?")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Cancel", use_container_width=True, key="del_mistake_cancel"):
            _close_dialog()
    with col2:
        if st.button("Delete", type="primary", use_container_width=True, key="del_mistake_confirm"):
            mistakes = _get_mistakes()
            if 0 <= index < len(mistakes):
                mistakes.pop(index)
                _set_mistakes(mistakes)
                _persist()
            st.session_state["_pending_dialog"] = None
            st.rerun()


@st.dialog("Clear review list?")
def _dlg_clear_mistakes():
    st.write(
        "Willst du die komplette Review-Liste dieses Dokuments leeren? "
        "Das kann nicht rückgängig gemacht werden."
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Cancel", use_container_width=True, key="clear_mistakes_cancel"):
            _close_dialog()
    with col2:
        if st.button("Clear all", type="primary", use_container_width=True, key="clear_mistakes_confirm"):
            _set_mistakes([])
            _persist()
            st.session_state["_pending_dialog"] = None
            st.rerun()


@st.dialog("Restart topic?")
def _dlg_restart_topic(topic: str):
    st.write(
        f"Willst du **{topic}** komplett neu starten? Der bisherige Fortschritt "
        "und die Konversation dieses Topics werden überschrieben."
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Cancel", use_container_width=True, key="restart_topic_cancel"):
            _close_dialog()
    with col2:
        if st.button("Restart", type="primary", use_container_width=True, key="restart_topic_confirm"):
            st.session_state["_pending_dialog"] = None
            st.session_state.pending_topic = topic
            st.rerun()


def _dispatch_dialog() -> None:
    pending = st.session_state.get("_pending_dialog")
    if not pending:
        return
    kind = pending.get("kind")
    if kind == "logout":
        _dlg_logout()
    elif kind == "delete_document":
        _dlg_delete_document(pending["name"])
    elif kind == "hide_topic":
        _dlg_hide_topic(pending["topic"])
    elif kind == "delete_mistake":
        _dlg_delete_mistake(pending["index"])
    elif kind == "clear_mistakes":
        _dlg_clear_mistakes()
    elif kind == "restart_topic":
        _dlg_restart_topic(pending["topic"])


# ── Auth ─────────────────────────────────────────────────────────────────────

user = st.session_state.get("user") or require_login()
if user is None:
    st.stop()
st.session_state["user"] = user

try:
    preferred = history.get_preferred_model(user["id"])
except Exception:
    preferred = None
if preferred in MODEL_OPTIONS:
    st.session_state.model = preferred

_dispatch_dialog()


# ── Upload & topic start ─────────────────────────────────────────────────────

def _handle_upload(uploaded_file) -> None:
    user = st.session_state["user"]
    text = load_document(uploaded_file, uploaded_file.name)
    if len(text.strip()) < 50:
        raise ValueError("Document appears empty or could not be parsed.")

    is_material, reason = check_is_learning_material(text)
    if not is_material:
        raise ValueError(reason)

    doc_name = uploaded_file.name

    existing_meta = _get_doc_meta(doc_name)
    if existing_meta and existing_meta["topics"]:
        topics = existing_meta["topics"]
        _get_rag(doc_name, text)
    else:
        rag = _get_rag(doc_name, text)
        agent = StudyBuddyAgent(text, rag)
        if st.session_state.model:
            agent.model = st.session_state.model
        agent.efforts = st.session_state.efforts_by_model
        try:
            topics = agent.extract_topics()
        except ValueError as e:
            _invalidate_rag(doc_name)
            raise ValueError(str(e)) from e

        st.session_state.agent = agent
        st.session_state.usage_saved = 0
        _save_usage()

    hidden = history.get_hidden_topics(user["id"], doc_name)
    topics = [
        t for t in topics
        if t not in hidden and t and "is_lecture_material" not in t.lower()
    ]
    if not topics:
        topics = ["Main Content"]

    if not existing_meta:
        history.create_session(
            user["id"], doc_name, text, PENDING_TOPIC_LABEL, topics,
            {}, [], [], None, None, 0,
        )

    _invalidate_docs_cache()
    _invalidate_doc_meta(doc_name)

    st.session_state.active_document = doc_name
    st.session_state.last_filename = doc_name
    st.session_state.document_text = text
    st.session_state.topics = topics
    st.session_state.agent = None
    st.session_state.conversation = []
    st.session_state.current_session_id = None
    st.session_state.active_topic = None
    st.session_state.app_state = "document_open"
    st.session_state.session_is_pending = False
    st.session_state.last_eval = None
    _set_mistakes([], doc_name)


def _select_topic(topic: str) -> None:
    user = st.session_state["user"]
    doc = st.session_state.active_document
    if not doc:
        return

    meta = _get_doc_meta(doc)
    existing = _find_session_in_meta(meta, topic)
    st.session_state.last_eval = None

    if existing:
        rag = _get_rag(doc, meta["document_text"])
        agent = StudyBuddyAgent(meta["document_text"], rag)
        if st.session_state.model:
            agent.model = st.session_state.model
        agent.efforts = st.session_state.efforts_by_model
        agent.concepts = existing["concepts"]
        agent.current_topic = existing["topic"]
        agent.current_concept = existing["current_concept"]
        agent.current_question = existing["current_question"]
        agent.follow_up_count = existing["follow_up_count"]
        agent.asked_questions = [
            m["content"] for m in existing["conversation"] if m["type"] == "question"
        ]
        st.session_state.agent = agent
        st.session_state.conversation = existing["conversation"]
        st.session_state.current_session_id = existing["id"]
        st.session_state.session_is_pending = False
        st.session_state.active_topic = topic
        st.session_state.usage_saved = 0
        agent.quiz_done = quiz_done_from_conversation(existing["conversation"])
        last = existing["conversation"][-1] if existing["conversation"] else {}
        if last.get("type") == "quiz" and last.get("quiz"):
            agent.current_quiz = dict(last["quiz"])

        if agent.topic_complete:
            st.session_state.app_state = "mastered"
        elif agent.concepts_complete:
            # A missing open question is generated when the quiz state renders
            st.session_state.app_state = "quiz"
        else:
            last_type = existing["conversation"][-1]["type"] if existing["conversation"] else None
            st.session_state.app_state = "questioning" if last_type == "question" else "feedback"
        return

    rag = _get_rag(doc, st.session_state.document_text)
    agent = StudyBuddyAgent(st.session_state.document_text, rag)
    if st.session_state.model:
        agent.model = st.session_state.model
    agent.efforts = st.session_state.efforts_by_model
    question = agent.start_topic(topic)
    n_concepts = len(agent.concepts)

    st.session_state.conversation = [
        {
            "role": "assistant",
            "content": (
                f"Let's explore **{topic}**. I've identified **{n_concepts} key concepts** "
                "you'll need to explain to master this topic. Explain in your own words — "
                "I'll give you feedback and dig deeper with follow-up questions."
            ),
            "type": "intro",
        },
        {"role": "assistant", "content": question, "type": "question", "model": agent.model},
    ]
    st.session_state.agent = agent
    st.session_state.active_topic = topic
    st.session_state.app_state = "questioning"
    st.session_state.usage_saved = 0

    placeholder = _find_placeholder_in_meta(meta)
    if placeholder:
        history.update_session(
            placeholder["id"], user["id"],
            agent.concepts, st.session_state.conversation, _get_mistakes(),
            agent.current_concept, agent.current_question, agent.follow_up_count,
            topic=topic,
        )
        st.session_state.current_session_id = placeholder["id"]
        st.session_state.session_is_pending = False
    else:
        st.session_state.current_session_id = history.create_session(
            user["id"], doc, st.session_state.document_text, topic,
            st.session_state.topics, agent.concepts, st.session_state.conversation,
            _get_mistakes(), agent.current_concept, agent.current_question,
            agent.follow_up_count,
        )

    _save_usage()
    _invalidate_docs_cache()
    _invalidate_doc_meta(doc)


def _start_next_quiz() -> bool:
    """Ask the next closed question. On failure, show the error and stay in
    "feedback", whose button tries again. Returns True on success."""
    agent = st.session_state.agent
    with st.spinner("Generating the next question…"):
        try:
            quiz = agent.generate_quiz()
        except Exception as e:
            st.session_state.app_state = "feedback"
            st.error(f"The AI service did not return a question ({e}). Please try again.")
            return False
    st.session_state.conversation.append({
        "role": "assistant", "content": quiz["question"], "type": "quiz",
        "quiz": quiz, "model": agent.model,
    })
    st.session_state.app_state = "quiz"
    return True


# ── Renderers ────────────────────────────────────────────────────────────────

QUIZ_LABELS = {"mcq": "Multiple Choice", "tf": "Wahr oder falsch?"}


def _quiz_answer_text(quiz: dict, choice) -> str:
    if quiz["kind"] == "mcq":
        return quiz["options"][choice]
    return "Wahr" if choice else "Falsch"


def _render_progress():
    agent = st.session_state.agent
    if not agent or not agent.concepts:
        return
    done, total = agent.progress
    st.progress(done / total, text=f"Mastered: {done}/{total}")
    with st.expander("Checklist"):
        for concept, covered in agent.concepts.items():
            st.markdown(f"{'✅' if covered else '⬜'} {concept}")
        for kind in QUIZ_KINDS:
            st.markdown(f"{'✅' if agent.quiz_done[kind] else '⬜'} {QUIZ_LABELS[kind]}")


def _render_feedback(content: str, score: str):
    if score == "correct":
        st.success(content)
    elif score == "partial":
        st.warning(content)
    else:
        st.error(content)


def _render_conversation():
    conversation = st.session_state.conversation
    for i, msg in enumerate(conversation):
        with st.chat_message(msg["role"]):
            t = msg["type"]
            if t == "question":
                st.markdown(f"**{msg['content']}**")
            elif t == "quiz":
                quiz = msg.get("quiz") or {}
                st.caption(QUIZ_LABELS.get(quiz.get("kind"), "Quiz"))
                st.markdown(f"**{msg['content']}**")
                # The open question's options are shown as the answer widget below
                is_open = i == len(conversation) - 1 and st.session_state.app_state == "quiz"
                if quiz.get("kind") == "mcq" and not is_open:
                    st.markdown("\n".join(f"- {o}" for o in quiz.get("options", [])))
            elif t == "intro":
                st.info(msg["content"])
            elif t == "mastered":
                st.success(msg["content"])
            elif t == "feedback":
                _render_feedback(msg["content"], msg.get("score", "partial"))
            elif t == "second_opinion":
                score = msg.get("score", "partial")
                _render_feedback(f"**Second opinion: {score}** — {msg['content']}", score)
            else:
                st.markdown(msg["content"])
            if msg.get("model"):
                st.caption(f"🤖 {msg['model']}")


def _context_share(model: str, prompt_tokens: int) -> float | None:
    limit = MODEL_OPTIONS.get(model)
    return prompt_tokens / limit if limit else None


def _render_block_usage():
    """Sidebar: tokens used in the current usage block and when it resets."""
    user = st.session_state["user"]
    hours = f"{USAGE_BLOCK_HOURS:g}"
    st.markdown(f"**⏱️ {hours}-Stunden-Block**")
    try:
        block = _block_usage_cached(user["id"])
    except Exception as e:
        print(f"[usage block] {type(e).__name__}: {e}")  # server log
        st.caption("Die Nutzung konnte gerade nicht geladen werden.")
        return
    if block["start"] is None:
        st.caption(f"Kein aktiver Block. Der nächste startet mit deiner nächsten Anfrage und läuft {hours} Stunden.")
        return
    per_model = block["per_model"]
    # Relative times only: the DB clock may not be in the viewer's time zone
    length = block["resets_at"] - block["start"]
    left = max(block["resets_at"] - block["now"], timedelta(0))
    minutes_left = int(left.total_seconds() // 60)
    st.progress(
        min(1.0, 1 - left / length),
        text=f"Erneuert in {minutes_left // 60} h {minutes_left % 60} min",
    )
    col_in, col_out = st.columns(2)
    col_in.metric("Input", f"{sum(p for p, _ in per_model.values()):,}")
    col_out.metric("Output", f"{sum(c for _, c in per_model.values()):,}")
    if len(per_model) > 1:
        for name, (tin, tout) in per_model.items():
            st.caption(f"🤖 {name}: {tin:,} in · {tout:,} out")


def _render_usage():
    user = st.session_state["user"]
    agent: StudyBuddyAgent = st.session_state.agent
    session_id = st.session_state.current_session_id
    per_model = {}
    if session_id:
        try:
            per_model = _usage_cached(user["id"], session_id)
        except Exception:
            per_model = {}
    if not per_model:
        for u in (agent.usage if agent and hasattr(agent, "usage") else []):
            tin, tout, peak = per_model.get(u["model"], (0, 0, 0))
            per_model[u["model"]] = (
                tin + u["prompt_tokens"],
                tout + u["completion_tokens"],
                max(peak, u["prompt_tokens"]),
            )
    if not per_model:
        st.caption("No tokens used yet.")
        return
    st.caption("This study session" if session_id else "This document")
    col_in, col_out = st.columns(2)
    col_in.metric("Input", f"{sum(t[0] for t in per_model.values()):,}")
    col_out.metric("Output", f"{sum(t[1] for t in per_model.values()):,}")
    if len(per_model) > 1:
        for name, (tin, tout, _) in per_model.items():
            st.caption(f"🤖 {name}: {tin:,} in · {tout:,} out")

    shares = {
        name: share
        for name, (_, _, peak) in per_model.items()
        if (share := _context_share(name, peak)) is not None
    }
    if agent and hasattr(agent, "usage") and agent.usage:
        last = agent.usage[-1]
        share = _context_share(last["model"], last["prompt_tokens"])
        if share is not None:
            st.progress(min(share, 1.0), text=f"Last request: {last['prompt_tokens']:,} tokens · {share:.2%} of the input limit")
        else:
            st.caption(f"Last request: {last['prompt_tokens']:,} input tokens (input limit not configured)")
    if shares:
        peak_model = max(shares, key=shares.get)
        peak_share = shares[peak_model]
        st.caption(f"Largest request: {peak_share:.2%} of the input limit ({peak_model})")
        if peak_share > CONTEXT_WARN_SHARE:
            st.warning(
                f"A request used more than {CONTEXT_WARN_SHARE:.0%} of the input limit. "
                "Models read long inputs less reliably, so questions and feedback may be less accurate."
            )


def _other_models() -> list[str]:
    current = st.session_state.agent.model if st.session_state.agent else st.session_state.model
    return [m for m in MODEL_OPTIONS if m != current]


def _scroll_to_latest():
    # A rerun jumps the page back to the top; bring the newest message into view instead.
    # The message count is part of the HTML so the script runs again for every new message.
    components.html(
        f"""<script>
        // messages: {len(st.session_state.conversation)}
        setTimeout(() => {{
            const msgs = window.parent.document.querySelectorAll('[data-testid="stChatMessage"]');
            if (msgs.length) msgs[msgs.length - 1].scrollIntoView({{behavior: "smooth", block: "start"}});
        }}, 150);
        </script>""",
        height=0,
    )


def _render_model_actions():
    agent: StudyBuddyAgent = st.session_state.agent
    others = _other_models()
    if not others:
        return
    last_eval = st.session_state.last_eval
    col1, col2 = st.columns(2)
    if st.session_state.app_state == "questioning":
        with col1:
            with st.popover("🔄 New question from another model", use_container_width=True):
                model = st.selectbox("Model", others, key="regen_model")
                if st.button("Generate question", key="regen_go", type="primary"):
                    with st.spinner(f"Asking {model}…"):
                        try:
                            q = agent.regenerate_question(model)
                        except Exception as e:
                            st.error(f"The AI service did not respond ({e}). Please try again.")
                            st.stop()
                    st.session_state.conversation.append(
                        {"role": "assistant", "content": q, "type": "question", "model": model}
                    )
                    _save_usage()
                    _persist()
                    st.rerun()
    if last_eval:
        with col2:
            with st.popover("🔍 Second opinion on my last answer", use_container_width=True):
                st.caption("Another model re-checks your last answer. Your progress stays unchanged.")
                model = st.selectbox("Model", others, key="opinion_model")
                if st.button("Get second opinion", key="opinion_go", type="primary"):
                    with st.spinner(f"Asking {model}…"):
                        try:
                            result = agent.second_opinion(last_eval["question"], last_eval["answer"], model)
                        except Exception as e:
                            st.error(f"The AI service did not respond ({e}). Please try again.")
                            st.stop()
                    st.session_state.conversation.append(
                        {
                            "role": "assistant",
                            "content": result.get("feedback", ""),
                            "type": "second_opinion",
                            "score": result.get("score", "partial"),
                            "model": model,
                        }
                    )
                    _save_usage()
                    _persist()
                    st.rerun()


def _render_topics_tab():
    if not st.session_state.topics:
        st.caption("No topics available for this document.")
        return

    doc = st.session_state.active_document
    meta = _get_doc_meta(doc) or {"sessions": []}

    sessions_by_topic: dict[str, dict] = {}
    for s in meta["sessions"]:
        if s["topic"] == PENDING_TOPIC_LABEL:
            continue
        sessions_by_topic.setdefault(s["topic"], s)

    for idx, topic in enumerate(st.session_state.topics):
        if not topic or "is_lecture_material" in topic.lower():
            continue
        with st.container(horizontal=True, horizontal_alignment="distribute",
                          vertical_alignment="center"):
            s = sessions_by_topic.get(topic)
            status = ""
            if s:
                concepts = s["concepts"] or {}
                quiz = quiz_done_from_conversation(s.get("conversation"))
                done = sum(1 for v in concepts.values() if v) + sum(quiz.values())
                total = len(concepts) + len(QUIZ_KINDS)
                if concepts and done == total:
                    status = "✅ mastered"
                elif concepts:
                    status = f"🟡 {done}/{total}"
                else:
                    status = "🟡 in progress"
            label = f"**{topic}**" + (f" — {status}" if status else "")
            if st.button(label, key=f"open_topic_{idx}", use_container_width=True):
                st.session_state.pending_topic = topic
                st.rerun()
            if st.button("🙈", key=f"hide_{idx}", help="Hide this topic and delete its sessions"):
                st.session_state["_pending_dialog"] = {"kind": "hide_topic", "topic": topic}
                st.rerun()


def _render_review_tab():
    mistakes = _get_mistakes()
    if not mistakes:
        st.caption("No mistakes recorded for this document yet.")
        return
    for i, m in enumerate(mistakes, 1):
        answer = m["answer"]
        if answer == "(übersprungen)":
            answer_display = "— *übersprungen* —"
        elif len(answer) > 200:
            answer_display = answer[:200].rstrip() + "…"
        else:
            answer_display = answer
        with st.container(horizontal=True, horizontal_alignment="distribute",
                          vertical_alignment="top"):
            with st.container():
                st.markdown(f"**{i}. {m['question']}**")
                st.caption(" · ".join(filter(None, [m.get("topic", ""), m.get("score", ""), m.get("model", "")])))
                st.markdown(f"Your answer: {answer_display}")
                if m.get("missing"):
                    st.markdown(f"Missing: {m['missing']}")
            if st.button("🗑️", key=f"del_mistake_{i}"):
                st.session_state["_pending_dialog"] = {"kind": "delete_mistake", "index": i - 1}
                st.rerun()
        if i < len(mistakes):
            st.divider()
    if st.button("Clear review list", key="clear_mistakes", use_container_width=True):
        st.session_state["_pending_dialog"] = {"kind": "clear_mistakes"}
        st.rerun()


def _render_hidden_tab():
    user = st.session_state["user"]
    doc = st.session_state.active_document
    hidden_list = history.get_hidden_topics_list(user["id"], doc)
    if not hidden_list:
        st.caption("No hidden topics for this document.")
        return
    for idx, topic in enumerate(hidden_list):
        with st.container(horizontal=True, horizontal_alignment="distribute",
                          vertical_alignment="center"):
            st.markdown(topic)
            if st.button("👁️", key=f"unhide_{idx}", help="Unhide this topic"):
                history.unhide_topic(user["id"], doc, topic)
                if topic not in st.session_state.topics:
                    st.session_state.topics.append(topic)
                _invalidate_doc_meta(doc)
                st.rerun()


# ── Pending topic action ────────────────────────────────────────────────────

if st.session_state.pending_topic is not None:
    topic = st.session_state.pending_topic
    st.session_state.pending_topic = None
    with st.spinner(f"Preparing **{topic}**… (first load may take a few seconds)"):
        try:
            _select_topic(topic)
        except Exception as e:
            st.error(f"Could not start topic: {e}")
    st.rerun()


# ── Sidebar ─────────────────────────────────────────────────────────────────

with st.sidebar:
    header_col1, header_col2 = st.columns([3, 2], vertical_alignment="top")
    with header_col1:
        st.markdown("### 📚 StudyBuddy")
        st.caption("AI Learning Partner")
    with header_col2:
        initials = "".join(part[0].upper() for part in user["username"].split()[:2]) or user["username"][:2].upper()
        st.markdown(
            f"""
            <div class="sb-user-badge">
                <div class="sb-avatar">{initials}</div>
                <div class="sb-user-meta">
                    <div class="sb-username" title="{user['username']}">{user['username']}</div>
                    <div class="sb-useremail" title="{user['email']}">{user['email']}</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    if st.button("🚪 Log out", use_container_width=True, key="sidebar_logout"):
        st.session_state["_pending_dialog"] = {"kind": "logout"}
        st.rerun()

    st.divider()

    if MODEL_OPTIONS:
        st.selectbox(
            "🤖 Model",
            list(MODEL_OPTIONS),
            key="model",
            on_change=_save_preferred_model,
            help="Pick the model you trust. You can switch at any time; your progress is kept.",
        )
    if st.session_state.model in MODEL_EFFORTS:
        model = st.session_state.model
        levels, default = MODEL_EFFORTS[model]
        current = st.session_state.efforts_by_model.get(model, default)
        st.session_state.efforts_by_model[model] = st.select_slider(
            "🧠 Effort",
            options=levels,
            value=current if current in levels else default,
            # One widget per model, so each model keeps its own choice
            key=f"effort_{model}",
            help="How thoroughly the model thinks before answering: none = no thinking, "
                 "xhigh = most careful but slowest and most tokens. Only levels this model "
                 "supports are listed; the preselected one is the model's own default.",
        )
    if st.session_state.agent and st.session_state.model:
        st.session_state.agent.model = st.session_state.model
        st.session_state.agent.efforts = st.session_state.efforts_by_model

    uploaded = st.file_uploader(
        "Upload lecture material",
        type=["pdf", "txt", "docx"],
        help="Supports PDF, plain text, and Word documents",
        key=f"uploader_{st.session_state._uploader_key}",
    )

    if uploaded is not None:
        st.session_state._uploader_had_file = True
        if uploaded.name != st.session_state.active_document:
            with st.spinner("Processing document…"):
                try:
                    _handle_upload(uploaded)
                    st.session_state._uploader_key += 1
                    st.session_state._uploader_had_file = False
                    st.rerun()
                except ValueError as e:
                    msg = str(e)
                    if any(k in msg for k in (
                        "Lernmaterial", "Rechnung", "Abrechnung",
                        "zu kurz", "not lecture", "Document appears empty",
                    )):
                        st.warning(msg)
                    else:
                        print(f"[upload ValueError] {msg}")
                        st.error(
                            "Das Dokument konnte nicht verarbeitet werden. "
                            "Bitte lade eine andere Datei hoch."
                        )
                except Exception:
                    import traceback
                    traceback.print_exc()
                    st.error(
                        "Das Dokument konnte nicht verarbeitet werden. "
                        "Bitte lade eine andere Datei hoch."
                    )
    elif st.session_state._uploader_had_file:
        st.session_state._uploader_had_file = False

    st.divider()

    st.markdown("#### 🕘 Meine Dokumente")
    docs = _get_docs()
    if not docs:
        st.caption("Noch keine Dokumente. Lade oben eine Datei hoch.")
    else:
        names = [d["document_name"] for d in docs]
        active = st.session_state.active_document
        with st.container(horizontal=True, horizontal_alignment="distribute",
                          vertical_alignment="center"):
            # The key includes the open document, so the widget is rebuilt with
            # the right value whenever a document is opened, uploaded or deleted.
            choice = st.selectbox(
                "Dokument",
                names,
                index=names.index(active) if active in names else None,
                format_func=lambda n: f"📄 {n}",
                placeholder="Dokument wählen…",
                label_visibility="collapsed",
                key=f"doc_select_{active}",
            )
            if st.button("🗑️", key="del_active_doc", disabled=active not in names,
                         help="Dieses Dokument und alle zugehörigen Sessions löschen"):
                st.session_state["_pending_dialog"] = {"kind": "delete_document", "name": active}
                st.rerun()
        if choice and choice != active:
            _open_document(choice)
            st.rerun()

    st.divider()
    render_stay_signed_in_help()
    st.caption(f"Model: {st.session_state.model or os.getenv('MODEL', 'not configured')}")
    _render_block_usage()
    with st.expander("📊 Token usage"):
        _render_usage()


# ── Main ─────────────────────────────────────────────────────────────────────

st.title("StudyBuddy — AI Learning Partner")

if st.session_state.active_document and st.session_state.app_state == "no_document":
    _open_document(st.session_state.active_document)

state = st.session_state.app_state

if state == "no_document":
    st.info("Wähle links ein Dokument aus oder lade eine neue Datei hoch.")
    st.markdown("""
**How it works:**

1. Upload your lecture notes or slides (PDF, Word, or plain text) — jedes Dokument ist ein eigener Chat.
2. StudyBuddy extracts the main topics automatically.
3. Inside a document, click a topic in the **Topics** tab.
4. Explain the concept in your own words — StudyBuddy evaluates it and asks targeted follow-ups until **every key concept** is mastered.
5. Use the **Review** tab for mistakes specific to this document, and **Hidden** to restore hidden topics.
""")

elif state == "document_open":
    st.subheader(f"📄 {st.session_state.active_document}")
    tab_topics, tab_review, tab_hidden = st.tabs(["📖 Topics", "❌ Review", "🙈 Hidden"])
    with tab_topics:
        _render_topics_tab()
    with tab_review:
        _render_review_tab()
    with tab_hidden:
        _render_hidden_tab()

elif state in ("questioning", "feedback", "quiz", "mastered"):
    top_col1, top_col2 = st.columns([1, 5])
    with top_col1:
        if st.button("← Topics", use_container_width=True, key="top_back_to_topics"):
            st.session_state.agent = None
            st.session_state.conversation = []
            st.session_state.current_session_id = None
            st.session_state.active_topic = None
            st.session_state.app_state = "document_open"
            st.rerun()
    with top_col2:
        st.markdown(f"### 💬 {st.session_state.active_topic}")

    _render_progress()
    _render_conversation()
    if state in ("questioning", "feedback"):
        _render_model_actions()
    _scroll_to_latest()

    if state == "questioning":
        answer = st.chat_input("Explain your understanding…", key="chat_input_answer")
        if answer:
            st.session_state.conversation.append(
                {"role": "user", "content": answer, "type": "answer"}
            )
            with st.chat_message("user"):
                st.markdown(answer)

            asked_question = st.session_state.agent.current_question
            asked_concept = st.session_state.agent.current_concept
            with st.spinner("Evaluating your answer…"):
                try:
                    result = st.session_state.agent.evaluate_answer(answer)
                except Exception as e:
                    st.session_state.conversation.pop()
                    st.error(f"The AI service did not respond ({e}). Please submit your answer again.")
                    st.stop()

            agent = st.session_state.agent
            st.session_state.last_eval = {"question": asked_question, "answer": answer}
            score = result.get("score", "partial")
            feedback = result.get("feedback", "")
            follow_up = result.get("follow_up")

            if score in ("partial", "incorrect"):
                current = _get_mistakes()
                current.append({
                    "topic": agent.current_topic,
                    "question": asked_question,
                    "concept": asked_concept,
                    "answer": answer,
                    "missing": result.get("missing"),
                    "score": score,
                    "model": agent.model,
                })
                _set_mistakes(current)
            elif score == "correct":
                # A correct answer usually comes on a follow-up whose wording
                # differs from the listed question, so match on the concept.
                # Entries saved before "concept" existed fall back to the question.
                mastered = {c for c, ok in agent.concepts.items() if ok}
                current = _get_mistakes()
                filtered = [
                    m for m in current
                    if not (
                        m.get("topic") == agent.current_topic
                        and (m.get("concept") in mastered or m.get("question") == asked_question)
                    )
                ]
                if len(filtered) != len(current):
                    _set_mistakes(filtered)

            if agent.concepts_complete:
                st.session_state.conversation.append({
                    "role": "assistant",
                    "content": f"Correct! {feedback}" if score == "correct" else feedback,
                    "type": "feedback", "score": score,
                    "model": agent.model,
                })
                st.session_state.conversation.append({
                    "role": "assistant",
                    "content": "Alle Konzepte erklärt! Zum Abschluss zwei kurze Fragen: "
                               "eine Multiple-Choice- und eine Wahr-oder-falsch-Frage.",
                    "type": "intro",
                })
                _start_next_quiz()
            elif score == "correct":
                st.session_state.conversation.append({
                    "role": "assistant",
                    "content": f"Correct! {feedback}",
                    "type": "feedback", "score": "correct",
                    "model": agent.model,
                })
                st.session_state.app_state = "feedback"
            elif follow_up:
                st.session_state.conversation.append({
                    "role": "assistant", "content": feedback, "type": "feedback",
                    "score": score, "model": agent.model,
                })
                st.session_state.conversation.append({
                    "role": "assistant", "content": follow_up, "type": "question",
                    "model": agent.model,
                })
            else:
                st.session_state.conversation.append({
                    "role": "assistant",
                    "content": f"{feedback} Let's approach this from another angle.",
                    "type": "feedback", "score": score,
                    "model": agent.model,
                })
                st.session_state.app_state = "feedback"

            _save_usage()
            _persist()
            st.rerun()

        if st.button("⏭️ Frage überspringen", key="skip_question"):
            agent = st.session_state.agent
            skipped_concept = agent.current_concept

            current = _get_mistakes()
            current.append({
                "topic": agent.current_topic,
                "question": agent.current_question,
                "concept": skipped_concept,
                "answer": "(übersprungen)",
                "missing": skipped_concept,
                "score": "incorrect",
                "model": agent.model,
            })
            _set_mistakes(current)

            with st.spinner("Generating next question…"):
                try:
                    q = agent.skip_question()
                except Exception as e:
                    st.error(f"The AI service did not respond ({e}). Please try again.")
                    st.stop()
            st.session_state.conversation.append({
                "role": "assistant",
                "content": f"⏭️ Okay, wir kommen später darauf zurück. {q}",
                "type": "question",
                "model": agent.model,
            })
            _save_usage()
            _persist()
            st.rerun()

    elif state == "feedback":
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("Next Question →", type="primary", use_container_width=True, key="feedback_next_question"):
                if st.session_state.agent.concepts_complete:
                    if not _start_next_quiz():
                        st.stop()
                    _save_usage()
                    _persist()
                    st.rerun()
                with st.spinner("Generating next question…"):
                    try:
                        q = st.session_state.agent.next_question()
                    except Exception as e:
                        st.error(f"The AI service did not respond ({e}). Please try again.")
                        st.stop()
                st.session_state.conversation.append(
                    {"role": "assistant", "content": q, "type": "question",
                     "model": st.session_state.agent.model}
                )
                st.session_state.app_state = "questioning"
                _save_usage()
                _persist()
                st.rerun()
        with col2:
            if st.button("← Topics", use_container_width=True, key="feedback_back_to_topics"):
                st.session_state.agent = None
                st.session_state.conversation = []
                st.session_state.current_session_id = None
                st.session_state.active_topic = None
                st.session_state.app_state = "document_open"
                st.rerun()
        with col3:
            if st.button("Restart Topic", use_container_width=True, key="feedback_restart_topic"):
                topic = st.session_state.agent.current_topic if st.session_state.agent else None
                if topic:
                    st.session_state["_pending_dialog"] = {"kind": "restart_topic", "topic": topic}
                    st.rerun()

    elif state == "quiz":
        agent = st.session_state.agent
        if agent.current_quiz is None:
            # Resumed session, or a question failed to generate before
            if _start_next_quiz():
                _save_usage()
                _persist()
                st.rerun()
            st.stop()
        quiz = agent.current_quiz
        # Keyed by position so a new question starts with nothing selected
        key = f"quiz_{len(st.session_state.conversation)}"
        choice = None
        if quiz["kind"] == "mcq":
            picked = st.radio("Your answer", range(len(quiz["options"])), index=None,
                              format_func=lambda i: quiz["options"][i], key=f"{key}_choice")
            if st.button("Antwort abgeben", type="primary", disabled=picked is None, key=f"{key}_submit"):
                choice = picked
        else:
            col_true, col_false = st.columns(2)
            if col_true.button("✅ Wahr", use_container_width=True, key=f"{key}_true"):
                choice = True
            if col_false.button("❌ Falsch", use_container_width=True, key=f"{key}_false"):
                choice = False

        if choice is not None:
            result = agent.answer_quiz(choice)
            chosen = _quiz_answer_text(quiz, choice)
            right = _quiz_answer_text(quiz, quiz["answer"])
            st.session_state.conversation.append({"role": "user", "content": chosen, "type": "answer"})
            if result["correct"]:
                content = f"Richtig! {quiz['explanation']}".strip()
                # Earlier wrong attempts of this kind are resolved now
                _set_mistakes([
                    m for m in _get_mistakes()
                    if not (m.get("topic") == agent.current_topic and m.get("quiz") == quiz["kind"])
                ])
            else:
                content = f"Leider falsch. Richtig ist: **{right}**. {quiz['explanation']}".strip()
                current = _get_mistakes()
                current.append({
                    "topic": agent.current_topic,
                    "question": quiz["question"],
                    "quiz": quiz["kind"],
                    "answer": chosen,
                    "missing": right,
                    "score": "incorrect",
                    "model": agent.model,
                })
                _set_mistakes(current)
            st.session_state.conversation.append({
                "role": "assistant", "content": content, "type": "feedback",
                "score": "correct" if result["correct"] else "incorrect",
                "quiz": quiz["kind"], "model": agent.model,
            })
            if agent.topic_complete:
                st.session_state.conversation.append({
                    "role": "assistant",
                    "content": f"🎉 You've mastered **{agent.current_topic}** — all key concepts and both final questions!",
                    "type": "mastered",
                })
                st.session_state.celebrate = True
                st.session_state.app_state = "mastered"
            else:
                # Next kind after a correct answer, a new one of the same kind after a wrong one
                _start_next_quiz()
            _save_usage()
            _persist()
            st.rerun()

    elif state == "mastered":
        if st.session_state.celebrate:
            st.balloons()
            st.session_state.celebrate = False
        col1, col2 = st.columns(2)
        with col1:
            if st.button("← Zurück zu Topics", type="primary", use_container_width=True, key="mastered_back_to_topics"):
                st.session_state.agent = None
                st.session_state.conversation = []
                st.session_state.current_session_id = None
                st.session_state.active_topic = None
                st.session_state.app_state = "document_open"
                st.rerun()
        with col2:
            if st.button("Restart This Topic", use_container_width=True, key="mastered_restart_topic"):
                topic = st.session_state.agent.current_topic if st.session_state.agent else None
                if topic:
                    st.session_state["_pending_dialog"] = {"kind": "restart_topic", "topic": topic}
                    st.rerun()