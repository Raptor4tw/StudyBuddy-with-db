import os

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from agent.study_agent import StudyBuddyAgent
from tools.document_loader import load_document
from tools.rag import RAGIndex
from auth import init_db
from auth_ui import require_login, logout
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
</style>
""", unsafe_allow_html=True)

init_db()
user = st.session_state.get("user") or require_login()
if user is None:
    st.stop()
st.session_state["user"] = user

# ── Session state ─────────────────────────────────────────────────────────────

for _k, _v in {
    "agent": None,
    "topics": [],
    "conversation": [],
    "app_state": "upload",
    "last_filename": None,
    "celebrate": False,
    "mistakes_by_user": {},
    "document_text": None,
    "current_session_id": None,
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


def _persist():
    """Save the current conversation/mistakes/concepts to the active session row."""
    if not st.session_state.current_session_id or not st.session_state.agent:
        return
    agent = st.session_state.agent
    history.update_session(
        st.session_state.current_session_id,
        user["id"],
        agent.concepts,
        st.session_state.conversation,
        st.session_state.mistakes_by_user.get(user["id"], []),
        agent.current_concept,
        agent.current_question,
        agent.follow_up_count,
    )


def _load_file(uploaded_file):
    text = load_document(uploaded_file, uploaded_file.name)
    if len(text.strip()) < 50:
        raise ValueError("Document appears empty or could not be parsed.")
    rag = RAGIndex(text)
    agent = StudyBuddyAgent(text, rag)
    topics = agent.extract_topics()
    st.session_state.agent = agent
    st.session_state.topics = topics
    st.session_state.conversation = []
    st.session_state.mistakes_by_user[user["id"]] = []
    st.session_state.document_text = text
    st.session_state.current_session_id = None
    st.session_state.app_state = "topic_select"
    st.session_state.last_filename = uploaded_file.name
    return len(text), topics


def _select_topic(topic: str):
    agent: StudyBuddyAgent = st.session_state.agent
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
    st.session_state.app_state = "questioning"
    st.session_state.current_session_id = history.create_session(
        user["id"],
        st.session_state.last_filename,
        st.session_state.document_text,
        topic,
        st.session_state.topics,
        agent.concepts,
        st.session_state.conversation,
        st.session_state.mistakes_by_user.get(user["id"], []),
        agent.current_concept,
        agent.current_question,
        agent.follow_up_count,
    )


def _resume_session(session_id: int):
    """Rebuild an agent + conversation from a previously saved session."""
    saved = history.load_session(session_id, user["id"])
    if saved is None:
        st.error("Could not find that saved session.")
        return

    rag = RAGIndex(saved["document_text"])
    agent = StudyBuddyAgent(saved["document_text"], rag)
    agent.concepts = saved["concepts"]
    agent.current_topic = saved["topic"]
    agent.current_concept = saved["current_concept"]
    agent.current_question = saved["current_question"]
    agent.follow_up_count = saved["follow_up_count"]
    agent.asked_questions = [
        m["content"] for m in saved["conversation"] if m["type"] == "question"
    ]

    st.session_state.agent = agent
    st.session_state.topics = saved["topics"]
    st.session_state.conversation = saved["conversation"]

    # Merge this session's mistakes into the user's current review list instead of
    # replacing it outright — dedupe by (topic, question) so re-resuming doesn't duplicate.
    existing = st.session_state.mistakes_by_user.get(user["id"], [])
    existing_keys = {(m["topic"], m["question"]) for m in existing}
    merged = existing + [
        m for m in saved["mistakes"] if (m["topic"], m["question"]) not in existing_keys
    ]
    st.session_state.mistakes_by_user[user["id"]] = merged

    st.session_state.document_text = saved["document_text"]
    st.session_state.last_filename = saved["document_name"]
    st.session_state.current_session_id = session_id

    if agent.topic_complete:
        st.session_state.app_state = "mastered"
    else:
        last_type = saved["conversation"][-1]["type"] if saved["conversation"] else None
        st.session_state.app_state = "questioning" if last_type == "question" else "feedback"


def _render_progress():
    agent: StudyBuddyAgent = st.session_state.agent
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


def _render_mistakes():
    mistakes = st.session_state.mistakes_by_user.get(user["id"], [])
    if not mistakes:
        st.caption("No mistakes recorded yet.")
        return
    with st.expander(f"❌ Review ({len(mistakes)})"):
        for i, m in enumerate(mistakes, 1):
            answer = m["answer"]
            if len(answer) > 200:
                answer = answer[:200].rstrip() + "…"
            col1, col2 = st.columns([5, 1])
            with col1:
                st.markdown(f"**{i}. {m['question']}**")
                st.caption(f"{m['topic']} · {m['score']}")
                st.markdown(f"Your answer: {answer}")
                if m["missing"]:
                    st.markdown(f"Missing: {m['missing']}")
            with col2:
                if st.button("🗑️", key=f"delete_mistake_{i}"):
                    st.session_state.mistakes_by_user[user["id"]].pop(i - 1)
                    _persist()
                    st.rerun()
            if i < len(mistakes):
                st.divider()
        if st.button("Clear review list", key="clear_mistakes", use_container_width=True):
            st.session_state.mistakes_by_user[user["id"]] = []
            _persist()
            st.rerun()


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    header_col1, header_col2 = st.columns([3, 2])
    with header_col1:
        st.markdown("### 📚 StudyBuddy")
        st.caption("AI Learning Partner")
    with header_col2:
        st.caption(f"👤 {user['username']}")
        if st.button("Log out", use_container_width=True):
            logout()
            st.rerun()

    st.divider()

    uploaded = st.file_uploader(
        "Upload lecture material",
        type=["pdf", "txt", "docx"],
        help="Supports PDF, plain text, and Word documents",
    )

    if uploaded and uploaded.name != st.session_state.last_filename:
        with st.spinner("Processing document…"):
            try:
                chars, topics = _load_file(uploaded)
                st.success(f"{chars:,} characters · {len(topics)} topics found", icon="✅")
            except Exception as e:
                st.error(f"Error: {e}")
                st.session_state.last_filename = None

    st.divider()

    tab_topics, tab_history, tab_review = st.tabs(["📖 Topics", "🕘 History", "❌ Review"])

    with tab_topics:
        if st.session_state.topics:
            for topic in st.session_state.topics:
                col1, col2 = st.columns([5, 1])
                with col1:
                    if st.button(topic, key=f"btn_{topic}", use_container_width=True):
                        try:
                            _select_topic(topic)
                        except Exception as e:
                            st.error(f"Could not start topic: {e}")
                        else:
                            st.rerun()
                with col2:
                    if st.button("🗑️", key=f"remove_topic_{topic}", help="Remove this topic and its history"):
                        matching = [s for s in history.list_sessions(user["id"]) if s["topic"] == topic]
                        for s in matching:
                            history.delete_session(s["id"], user["id"])
                        st.session_state.topics.remove(topic)
                        st.session_state.mistakes_by_user[user["id"]] = [
                            m for m in st.session_state.mistakes_by_user.get(user["id"], [])
                            if m["topic"] != topic
                        ]
                        if st.session_state.agent and st.session_state.agent.current_topic == topic:
                            st.session_state.agent = None
                            st.session_state.conversation = []
                            st.session_state.current_session_id = None
                            st.session_state.app_state = "topic_select"
                        st.rerun()
        else:
            st.caption("Upload a document to see topics here.")

    with tab_history:
        sessions = history.list_sessions(user["id"])
        if not sessions:
            st.caption("No past sessions yet.")
        for s in sessions:
            col1, col2 = st.columns([5, 1])
            with col1:
                label = f"{s['document_name']} · {s['topic']}"
                if st.button(label, key=f"resume_{s['id']}", use_container_width=True):
                    _resume_session(s["id"])
                    st.rerun()
            with col2:
                if st.button("🗑️", key=f"delete_{s['id']}"):
                    history.delete_session(s["id"], user["id"])
                    if s["topic"] in st.session_state.topics:
                        st.session_state.topics.remove(s["topic"])
                    st.session_state.mistakes_by_user[user["id"]] = [
                        m for m in st.session_state.mistakes_by_user.get(user["id"], [])
                        if m["topic"] != s["topic"]
                    ]
                    if st.session_state.agent and st.session_state.agent.current_topic == s["topic"]:
                        st.session_state.agent = None
                        st.session_state.conversation = []
                        st.session_state.current_session_id = None
                        st.session_state.app_state = "topic_select"
                    st.rerun()

    with tab_review:
        _render_mistakes()

    st.divider()
    st.caption(f"Model: {os.getenv('MODEL', 'not configured')}")


# ── Main ──────────────────────────────────────────────────────────────────────

st.title("StudyBuddy — AI Learning Partner")

state = st.session_state.app_state

if state == "upload":
    st.info("Upload a PDF, TXT, or DOCX file in the sidebar to get started.")
    st.markdown("""
**How it works:**

1. Upload your lecture notes or slides (PDF, Word, or plain text)
2. StudyBuddy extracts the main topics automatically
3. Select a topic — StudyBuddy identifies its key concepts and asks you a question **from the material only**
4. Explain the concept in your own words
5. Get instant feedback and targeted follow-up questions until **every key concept** of the topic is mastered
""")

elif state == "topic_select":
    st.info("Please choose a topic from the **Topics** tab in the sidebar to start a session.")

elif state in ("questioning", "feedback", "mastered"):
    _render_progress()
    _render_conversation()

    if state == "questioning":
        answer = st.chat_input("Explain your understanding…")
        if answer:
            st.session_state.conversation.append(
                {"role": "user", "content": answer, "type": "answer"}
            )
            asked_question = st.session_state.agent.current_question
            with st.spinner("Evaluating your answer…"):
                try:
                    result = st.session_state.agent.evaluate_answer(answer)
                except Exception as e:
                    st.session_state.conversation.pop()
                    st.error(f"The AI service did not respond ({e}). Please submit your answer again.")
                    st.stop()

            agent: StudyBuddyAgent = st.session_state.agent
            score = result.get("score", "partial")
            feedback = result.get("feedback", "")
            follow_up = result.get("follow_up")

            if score in ("partial", "incorrect"):
                st.session_state.mistakes_by_user.setdefault(user["id"], []).append(
                    {
                        "topic": agent.current_topic,
                        "question": asked_question,
                        "answer": answer,
                        "missing": result.get("missing"),
                        "score": score,
                    }
                )

            if agent.topic_complete:
                st.session_state.conversation.append(
                    {
                        "role": "assistant",
                        "content": f"Correct! {feedback}" if score == "correct" else feedback,
                        "type": "feedback",
                        "score": score,
                    }
                )
                st.session_state.conversation.append(
                    {
                        "role": "assistant",
                        "content": f"🎉 You've explained all key concepts of **{agent.current_topic}** — topic mastered!",
                        "type": "mastered",
                    }
                )
                st.session_state.celebrate = True
                st.session_state.app_state = "mastered"

            elif score == "correct":
                st.session_state.conversation.append(
                    {
                        "role": "assistant",
                        "content": f"Correct! {feedback}",
                        "type": "feedback",
                        "score": "correct",
                    }
                )
                st.session_state.app_state = "feedback"

            elif follow_up:
                st.session_state.conversation.append(
                    {"role": "assistant", "content": feedback, "type": "feedback", "score": score}
                )
                st.session_state.conversation.append(
                    {"role": "assistant", "content": follow_up, "type": "question"}
                )

            else:
                st.session_state.conversation.append(
                    {
                        "role": "assistant",
                        "content": f"{feedback} Let's approach this from another angle.",
                        "type": "feedback",
                        "score": score,
                    }
                )
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
                st.session_state.conversation.append(
                    {"role": "assistant", "content": q, "type": "question"}
                )
                st.session_state.app_state = "questioning"
                _persist()
                st.rerun()
        with col2:
            if st.button("Change Topic", use_container_width=True):
                st.session_state.conversation = []
                st.session_state.app_state = "topic_select"
                st.session_state.current_session_id = None
                st.rerun()
        with col3:
            if st.button("Restart Topic", use_container_width=True):
                topic = st.session_state.agent.current_topic
                if topic:
                    with st.spinner("Restarting…"):
                        try:
                            _select_topic(topic)
                        except Exception as e:
                            st.error(f"Could not restart: {e}")
                        else:
                            st.rerun()

    elif state == "mastered":
        if st.session_state.celebrate:
            st.balloons()
            st.session_state.celebrate = False
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Choose Next Topic →", type="primary", use_container_width=True):
                st.session_state.conversation = []
                st.session_state.app_state = "topic_select"
                st.session_state.current_session_id = None
                st.rerun()
        with col2:
            if st.button("Restart This Topic", use_container_width=True):
                topic = st.session_state.agent.current_topic
                if topic:
                    with st.spinner("Restarting…"):
                        try:
                            _select_topic(topic)
                        except Exception as e:
                            st.error(f"Could not restart: {e}")
                        else:
                            st.rerun()