import os

import streamlit as st
from dotenv import load_dotenv

from agent.study_agent import StudyBuddyAgent
from tools.document_loader import load_document
from tools.rag import RAGIndex

load_dotenv()

# On Streamlit Community Cloud there is no .env file - credentials come from st.secrets.
# Mirror them into the environment so os.getenv() works the same locally and in the cloud.
try:
    for _key, _value in st.secrets.items():
        os.environ.setdefault(_key, str(_value))
except Exception:
    pass  # No secrets.toml configured (normal for local and Docker runs)

st.set_page_config(page_title="StudyBuddy", page_icon="📚", layout="wide")

# ── Session state ─────────────────────────────────────────────────────────────

for _k, _v in {
    "agent": None,
    "topics": [],
    "conversation": [],
    "app_state": "upload",   # upload | topic_select | questioning | feedback | mastered
    "last_filename": None,
    "celebrate": False,
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


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


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("📚 StudyBuddy")
    st.caption("AI Learning Partner")
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
                st.success(f"Loaded {chars:,} characters · {len(topics)} topics found")
            except Exception as e:
                st.error(f"Error: {e}")
                st.session_state.last_filename = None

    if st.session_state.topics:
        st.divider()
        st.subheader("Topics")
        for topic in st.session_state.topics:
            if st.button(topic, key=f"btn_{topic}", use_container_width=True):
                with st.spinner("Preparing first question…"):
                    try:
                        _select_topic(topic)
                    except Exception as e:
                        st.error(f"Could not start topic: {e}")
                    else:
                        st.rerun()

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
    st.info("Please choose a topic from the **Topics** list in the sidebar to start a session.")

elif state in ("questioning", "feedback", "mastered"):
    _render_progress()
    _render_conversation()

    if state == "questioning":
        answer = st.chat_input("Explain your understanding…")
        if answer:
            st.session_state.conversation.append(
                {"role": "user", "content": answer, "type": "answer"}
            )
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
                # Stay in "questioning" — follow_up is now the active question

            else:
                # Max follow-ups reached — retry the concept from a fresh angle
                st.session_state.conversation.append(
                    {
                        "role": "assistant",
                        "content": f"{feedback} Let's approach this from another angle.",
                        "type": "feedback",
                        "score": score,
                    }
                )
                st.session_state.app_state = "feedback"

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
                st.rerun()
        with col2:
            if st.button("Change Topic", use_container_width=True):
                st.session_state.conversation = []
                st.session_state.app_state = "topic_select"
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
