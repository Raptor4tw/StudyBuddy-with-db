import os

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from agent.study_agent import StudyBuddyAgent
from tools.document_loader import load_document
from tools.rag import RAGIndex
from auth import init_db
from auth_ui import require_login, logout, render_stay_signed_in_help
import history

try:
    for _key, _value in st.secrets.items():
        os.environ.setdefault(_key, str(_value))
except Exception:
    pass

st.set_page_config(page_title="StudyBuddy", page_icon="📚", layout="wide")

st.markdown("""
<style>
    section[data-testid="stSidebar"] button {
        text-align: left;
        justify-content: flex-start;
    }
    div[data-testid="stChatMessage"] {
        border-radius: 10px;
    }
    section[data-testid="stSidebar"] .stButton > button {
        font-size: 0.9rem;
    }
</style>
""", unsafe_allow_html=True)

init_db()

PENDING_TOPIC_LABEL = "Uploaded – choose a topic"

# ── Session state ─────────────────────────────────────────────────────────────

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
    "_docs_cache": None,
    "_doc_meta_cache": {},
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ── Helper functions ─────────────────────────────────────────────────────────

@st.cache_resource(show_spinner=False)
def _build_rag_cached(doc_name: str, document_text: str) -> RAGIndex:
    return RAGIndex(document_text)


def _get_rag(doc_name: str, document_text: str) -> RAGIndex:
    cache = st.session_state._rag_cache
    if doc_name in cache:
        return cache[doc_name]
    with st.spinner("Loading document model… (first time only)"):
        rag = _build_rag_cached(doc_name, document_text)
    cache[doc_name] = rag
    return rag


def _invalidate_docs_cache():
    st.session_state._docs_cache = None


def _invalidate_doc_meta(doc_name: str | None = None):
    if doc_name is None:
        st.session_state._doc_meta_cache.clear()
    else:
        st.session_state._doc_meta_cache.pop(doc_name, None)


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
    cache = st.session_state._doc_meta_cache
    if doc_name not in cache:
        cache[doc_name] = history.load_document_meta(user["id"], doc_name)
    return cache[doc_name]


def _get_docs() -> list[dict]:
    user = st.session_state.get("user")
    if not user:
        return []
    if st.session_state._docs_cache is None:
        st.session_state._docs_cache = history.list_documents(user["id"])
    return st.session_state._docs_cache


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
    st.session_state.topics = [t for t in topics_snapshot if t not in hidden]

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


# ── Dialog infrastructure ─────────────────────────────────────────────────────

def _close_dialog() -> None:
    st.session_state["_pending_dialog"] = None
    st.rerun()


@st.dialog("Log out?")
def _dlg_logout():
    st.write("Bist du sicher, dass du dich abmelden möchtest?")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Cancel", use_container_width=True):
            _close_dialog()
    with col2:
        if st.button("Log out", type="primary", use_container_width=True):
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
        if st.button("Cancel", use_container_width=True):
            _close_dialog()
    with col2:
        if st.button("Delete", type="primary", use_container_width=True):
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
        if st.button("Cancel", use_container_width=True):
            _close_dialog()
    with col2:
        if st.button("Hide", type="primary", use_container_width=True):
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
        if st.button("Cancel", use_container_width=True):
            _close_dialog()
    with col2:
        if st.button("Delete", type="primary", use_container_width=True):
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
        if st.button("Cancel", use_container_width=True):
            _close_dialog()
    with col2:
        if st.button("Clear all", type="primary", use_container_width=True):
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
        if st.button("Cancel", use_container_width=True):
            _close_dialog()
    with col2:
        if st.button("Restart", type="primary", use_container_width=True):
            st.session_state["_pending_dialog"] = None
            st.session_state.pending_topic = topic
            st.rerun()


def _dispatch_dialog() -> None:
    """Open at most one dialog per rerun based on `_pending_dialog`."""
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


# ── Auth ──────────────────────────────────────────────────────────────────────

user = st.session_state.get("user") or require_login()
if user is None:
    st.stop()
st.session_state["user"] = user

_dispatch_dialog()


# ── Upload & topic start ──────────────────────────────────────────────────────

def _handle_upload(uploaded_file) -> None:
    user = st.session_state["user"]
    text = load_document(uploaded_file, uploaded_file.name)
    if len(text.strip()) < 50:
        raise ValueError("Document appears empty or could not be parsed.")

    doc_name = uploaded_file.name

    existing_meta = history.load_document_meta(user["id"], doc_name)
    if existing_meta and existing_meta["topics"]:
        topics = existing_meta["topics"]
        _get_rag(doc_name, text)
    else:
        rag = _get_rag(doc_name, text)
        agent = StudyBuddyAgent(text, rag)
        topics = agent.extract_topics()

    hidden = history.get_hidden_topics(user["id"], doc_name)
    topics = [t for t in topics if t not in hidden]

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
    _set_mistakes([], doc_name)


def _select_topic(topic: str) -> None:
    user = st.session_state["user"]
    doc = st.session_state.active_document
    if not doc:
        return

    meta = _get_doc_meta(doc)
    existing = _find_session_in_meta(meta, topic)

    if existing:
        rag = _get_rag(doc, meta["document_text"])
        agent = StudyBuddyAgent(meta["document_text"], rag)
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

        if agent.topic_complete:
            st.session_state.app_state = "mastered"
        else:
            last_type = existing["conversation"][-1]["type"] if existing["conversation"] else None
            st.session_state.app_state = "questioning" if last_type == "question" else "feedback"
        return

    rag = _get_rag(doc, st.session_state.document_text)
    agent = StudyBuddyAgent(st.session_state.document_text, rag)
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
        {"role": "assistant", "content": question, "type": "question"},
    ]
    st.session_state.agent = agent
    st.session_state.active_topic = topic
    st.session_state.app_state = "questioning"

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

    _invalidate_docs_cache()
    _invalidate_doc_meta(doc)


# ── Renderers ─────────────────────────────────────────────────────────────────

def _render_progress():
    agent = st.session_state.agent
    if not agent or not agent.concepts:
        return
    done, total = agent.progress
    st.progress(done / total, text=f"Concepts mastered: {done}/{total}")
    with st.expander("Concept checklist"):
        for concept, covered in agent.concepts.items():
            st.markdown(f"{'✅' if covered else '⬜'} {concept}")


def _render_conversation():
    for msg in st.session_state.conversation:
        with st.chat_message(msg["role"]):
            t = msg["type"]
            if t == "question":
                st.markdown(f"**{msg['content']}**")
            elif t == "intro":
                st.info(msg["content"])
            elif t == "mastered":
                st.success(msg["content"])
            elif t == "feedback":
                score = msg.get("score", "partial")
                if score == "correct":
                    st.success(msg["content"])
                elif score == "partial":
                    st.warning(msg["content"])
                else:
                    st.error(msg["content"])
            else:
                st.markdown(msg["content"])


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

    for topic in st.session_state.topics:
        col1, col2 = st.columns([6, 1])
        with col1:
            s = sessions_by_topic.get(topic)
            status = ""
            if s:
                concepts = s["concepts"] or {}
                if concepts and all(concepts.values()):
                    status = "✅ mastered"
                elif concepts:
                    done = sum(1 for v in concepts.values() if v)
                    status = f"🟡 {done}/{len(concepts)}"
                else:
                    status = "🟡 in progress"
            label = f"**{topic}**" + (f" — {status}" if status else "")
            if st.button(label, key=f"open_topic_{topic}", use_container_width=True):
                st.session_state.pending_topic = topic
                st.rerun()
        with col2:
            if st.button("🙈", key=f"hide_{topic}", help="Hide this topic and delete its sessions"):
                st.session_state["_pending_dialog"] = {"kind": "hide_topic", "topic": topic}
                st.rerun()


def _render_review_tab():
    mistakes = _get_mistakes()
    if not mistakes:
        st.caption("No mistakes recorded for this document yet.")
        return
    for i, m in enumerate(mistakes, 1):
        answer = m["answer"]
        if len(answer) > 200:
            answer = answer[:200].rstrip() + "…"
        col1, col2 = st.columns([5, 1])
        with col1:
            st.markdown(f"**{i}. {m['question']}**")
            st.caption(f"{m['topic']} · {m['score']}")
            st.markdown(f"Your answer: {answer}")
            if m.get("missing"):
                st.markdown(f"Missing: {m['missing']}")
        with col2:
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
    for topic in hidden_list:
        col1, col2 = st.columns([5, 1])
        with col1:
            st.markdown(topic)
        with col2:
            if st.button("👁️", key=f"unhide_{topic}", help="Unhide this topic"):
                history.unhide_topic(user["id"], doc, topic)
                if topic not in st.session_state.topics:
                    st.session_state.topics.append(topic)
                _invalidate_doc_meta(doc)
                st.rerun()


# ── Pending topic action ──────────────────────────────────────────────────────

if st.session_state.pending_topic is not None:
    topic = st.session_state.pending_topic
    st.session_state.pending_topic = None
    with st.spinner(f"Preparing **{topic}**… (first load may take a few seconds)"):
        try:
            _select_topic(topic)
        except Exception as e:
            st.error(f"Could not start topic: {e}")
    st.rerun()


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    header_col1, header_col2 = st.columns([3, 2])
    with header_col1:
        st.markdown("### 📚 StudyBuddy")
        st.caption("AI Learning Partner")
    with header_col2:
        initials = "".join(part[0].upper() for part in user["username"].split()[:2]) or user["username"][:2].upper()
        st.markdown(
            f"""
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
                <div style="
                    width:32px;height:32px;border-radius:50%;
                    background:linear-gradient(135deg,#4f46e5,#7c3aed);
                    color:white;display:flex;align-items:center;justify-content:center;
                    font-weight:600;font-size:13px;">
                    {initials}
                </div>
                <div style="font-size:0.85rem;">
                    <div style="font-weight:600;">{user["username"]}</div>
                    <div style="color:#8a8a8a;font-size:0.75rem;">{user["email"]}</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("Log out", use_container_width=True):
            st.session_state["_pending_dialog"] = {"kind": "logout"}
            st.rerun()

    st.divider()

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
                except Exception as e:
                    st.error(f"Error: {e}")
    elif st.session_state._uploader_had_file:
        st.session_state._uploader_had_file = False

    st.divider()

    st.markdown("#### 🕘 Meine Dokumente")
    docs = _get_docs()
    if not docs:
        st.caption("Noch keine Dokumente. Lade oben eine Datei hoch.")
    for d in docs:
        name = d["document_name"]
        is_active = (name == st.session_state.active_document)
        label = ("▶ " if is_active else "📄 ") + name
        col1, col2 = st.columns([6, 1])
        with col1:
            if st.button(label, key=f"open_doc_{name}", use_container_width=True):
                if not is_active:
                    _open_document(name)
                    st.rerun()
        with col2:
            if st.button("🗑️", key=f"del_doc_{name}", help="Dieses Dokument und alle zugehörigen Sessions löschen"):
                st.session_state["_pending_dialog"] = {"kind": "delete_document", "name": name}
                st.rerun()

    st.divider()
    render_stay_signed_in_help()
    st.caption(f"Model: {os.getenv('MODEL', 'not configured')}")


# ── Main ──────────────────────────────────────────────────────────────────────

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

elif state in ("questioning", "feedback", "mastered"):
    top_col1, top_col2 = st.columns([1, 5])
    with top_col1:
        if st.button("← Topics", use_container_width=True):
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

    if state == "questioning":
        answer = st.chat_input("Explain your understanding…")
        if answer:
            st.session_state.conversation.append(
                {"role": "user", "content": answer, "type": "answer"}
            )
            with st.chat_message("user"):
                st.markdown(answer)

            asked_question = st.session_state.agent.current_question
            with st.spinner("Evaluating your answer…"):
                try:
                    result = st.session_state.agent.evaluate_answer(answer)
                except Exception as e:
                    st.session_state.conversation.pop()
                    st.error(f"The AI service did not respond ({e}). Please submit your answer again.")
                    st.stop()

            agent = st.session_state.agent
            score = result.get("score", "partial")
            feedback = result.get("feedback", "")
            follow_up = result.get("follow_up")

            if score in ("partial", "incorrect"):
                current = _get_mistakes()
                current.append({
                    "topic": agent.current_topic,
                    "question": asked_question,
                    "answer": answer,
                    "missing": result.get("missing"),
                    "score": score,
                })
                _set_mistakes(current)

            if agent.topic_complete:
                st.session_state.conversation.append({
                    "role": "assistant",
                    "content": f"Correct! {feedback}" if score == "correct" else feedback,
                    "type": "feedback", "score": score,
                })
                st.session_state.conversation.append({
                    "role": "assistant",
                    "content": f"🎉 You've explained all key concepts of **{agent.current_topic}** — topic mastered!",
                    "type": "mastered",
                })
                st.session_state.celebrate = True
                st.session_state.app_state = "mastered"
            elif score == "correct":
                st.session_state.conversation.append({
                    "role": "assistant",
                    "content": f"Correct! {feedback}",
                    "type": "feedback", "score": "correct",
                })
                st.session_state.app_state = "feedback"
            elif follow_up:
                st.session_state.conversation.append({
                    "role": "assistant", "content": feedback, "type": "feedback", "score": score,
                })
                st.session_state.conversation.append({
                    "role": "assistant", "content": follow_up, "type": "question",
                })
            else:
                st.session_state.conversation.append({
                    "role": "assistant",
                    "content": f"{feedback} Let's approach this from another angle.",
                    "type": "feedback", "score": score,
                })
                st.session_state.app_state = "feedback"

            _persist()
            st.rerun()

    elif state == "feedback":
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("Next Question →", type="primary", use_container_width=True):
                with st.spinner("Generating next question…"):
                    try:
                        q = st.session_state.agent.next_question()
                    except Exception as e:
                        st.error(f"The AI service did not respond ({e}). Please try again.")
                        st.stop()
                st.session_state.conversation.append({
                    "role": "assistant", "content": q, "type": "question",
                })
                st.session_state.app_state = "questioning"
                _persist()
                st.rerun()
        with col2:
            if st.button("← Topics", use_container_width=True):
                st.session_state.agent = None
                st.session_state.conversation = []
                st.session_state.current_session_id = None
                st.session_state.active_topic = None
                st.session_state.app_state = "document_open"
                st.rerun()
        with col3:
            if st.button("Restart Topic", use_container_width=True):
                topic = st.session_state.agent.current_topic if st.session_state.agent else None
                if topic:
                    st.session_state["_pending_dialog"] = {"kind": "restart_topic", "topic": topic}
                    st.rerun()

    elif state == "mastered":
        if st.session_state.celebrate:
            st.balloons()
            st.session_state.celebrate = False
        col1, col2 = st.columns(2)
        with col1:
            if st.button("← Zurück zu Topics", type="primary", use_container_width=True):
                st.session_state.agent = None
                st.session_state.conversation = []
                st.session_state.current_session_id = None
                st.session_state.active_topic = None
                st.session_state.app_state = "document_open"
                st.rerun()
        with col2:
            if st.button("Restart This Topic", use_container_width=True):
                topic = st.session_state.agent.current_topic if st.session_state.agent else None
                if topic:
                    st.session_state["_pending_dialog"] = {"kind": "restart_topic", "topic": topic}
                    st.rerun()