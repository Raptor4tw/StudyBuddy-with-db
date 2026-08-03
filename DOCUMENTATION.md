# StudyBuddy - Technical Documentation

StudyBuddy is an AI learning partner developed as a Master's project at the Westphalian University of Applied Sciences (Institute for Internet Security). It lets students upload their own lecture materials and verifies their understanding through an iterative Socratic dialogue: the agent asks comprehension questions grounded strictly in the uploaded material, evaluates the student's explanations, and keeps asking targeted follow-up questions until every key concept of a topic has been explained.

**Team:** Ahmad Morad, Ibrahim Zarouri, Ibrahim Shaban
**Supervisor:** Prof. Dr.-Ing. Tobias Urban

---

## 1. Architecture Overview

```
┌────────────────────────────────────────────────────────────┐
│                   app.py (Streamlit UI)                    │
│      state machine, chat rendering, progress display       │
└─────────────┬───────────────────────────────┬──────────────┘
              │                               │
              ▼                               ▼
┌──────────────────────────┐   ┌─────────────────────────────┐
│  tools/document_loader   │   │    agent/StudyBuddyAgent    │
│ PDF / DOCX / TXT → text  │   │  topic & concept extraction │
└─────────────┬────────────┘   │  question generation        │
              │                │  answer evaluation          │
              ▼                │  mastery tracking           │
┌──────────────────────────┐   └──────┬──────────────┬───────┘
│      tools/RAGIndex      │◄─────────┘              │
│  chunking, embeddings,   │                         │
│  FAISS similarity search │             ┌───────────▼────────────┐
└──────────────────────────┘             │  LLM API (OpenAI-      │
                                         │  compatible endpoint)  │
                                         └────────────────────────┘
```

### Project structure

```
Study-buddy/
├── app.py                  # Streamlit UI and application state machine
├── agent/
│   ├── prompts.py          # All LLM prompt templates
│   └── study_agent.py      # StudyBuddyAgent: core logic and LLM calls
├── tools/
│   ├── document_loader.py  # PDF / DOCX / TXT parsing
│   └── rag.py              # Text chunking, embeddings, FAISS index
├── tests/
│   ├── test_study_agent.py # Unit tests for parsing, sampling, concept matching
│   └── test_tools.py       # Unit tests for chunking and document loading
├── docs/
│   └── index.html          # Project website (served via GitHub Pages)
├── .streamlit/
│   └── config.toml         # Upload limit and telemetry settings
├── conftest.py             # Pytest path configuration
├── requirements.txt
├── Dockerfile              # Container image (CPU-only torch, model baked in)
├── docker-compose.yml      # One-command containerized start
├── .dockerignore           # Keeps secrets and caches out of the image
├── .env.example            # Template for API configuration
└── README.md               # Setup and usage instructions
```

---

## 2. Processing Pipeline

1. **Upload & parsing** - the student uploads a PDF, DOCX, or TXT file. `document_loader.load_document()` extracts plain text (via `pypdf` for PDFs, `python-docx` for Word documents). Documents yielding fewer than 50 characters are rejected as unparseable.

2. **Indexing (RAG)** - `RAGIndex` splits the text into chunks of 600 characters with 80 characters of overlap, embeds them with the `all-MiniLM-L6-v2` sentence-transformer model (normalized embeddings), and stores them in a FAISS `IndexFlatIP` index (inner product ≙ cosine similarity).

3. **Topic extraction** - because long documents exceed the prompt budget, `_sample_text()` takes evenly spaced samples from the beginning, middle, and end of the document (~4,800 characters total). The LLM returns 3-8 topic names as a JSON array.

4. **Concept extraction** - when the student selects a topic, the agent retrieves the most relevant chunks for that topic and asks the LLM for the **3-5 key concepts** a student must explain to prove full understanding. These become the mastery checklist.

5. **Question loop** - the agent picks the first uncovered concept, retrieves context for it, and generates one focused comprehension question (single-focus, under 25 words, never repeating previously asked questions).

6. **Evaluation** - the student's answer is evaluated against retrieved context only. The LLM returns structured JSON: a score (`correct` / `partial` / `incorrect`), encouraging feedback, an optional follow-up question, and the list of key concepts the answer demonstrably covered.

7. **Mastery tracking** - covered concepts are checked off. Weak answers trigger targeted follow-ups (max. 2 per question, then the concept is retried from a fresh angle). The topic is **mastered** only when every key concept has been covered - implementing the assignment requirement of an iterative dialogue *"until a topic has been fully explained"*.

---

## 3. Application State Machine (`app.py`)

```
 upload ──► topic_select ──► questioning ◄──► feedback
                 ▲                │
                 │                ▼
                 └───────────  mastered
```

| State | Meaning | Transitions |
|---|---|---|
| `upload` | No document loaded yet | → `topic_select` after successful upload |
| `topic_select` | Topics listed in sidebar | → `questioning` when a topic is chosen |
| `questioning` | A question is active; chat input shown | → stays on follow-up, → `feedback` or `mastered` after evaluation |
| `feedback` | Answer evaluated; action buttons shown | → `questioning` (next question), `topic_select` (change), or restart |
| `mastered` | All key concepts covered 🎉 | → `topic_select` or restart topic |

Conversation history, the agent instance, and all state live in `st.session_state`.

---

## 4. The Agent (`agent/study_agent.py`)

`StudyBuddyAgent` holds the LLM client and the per-topic learning state:

| Attribute | Purpose |
|---|---|
| `concepts: dict[str, bool]` | Mastery checklist - concept name → covered? |
| `current_concept` | The concept the active question targets |
| `asked_questions: list[str]` | History passed to the LLM to prevent repeated questions |
| `follow_up_count` / `max_follow_ups` | Limits follow-ups per question (2) before retrying from a new angle |

Key methods:

- `extract_topics()` - document-wide topic list (JSON, with plain-text fallback parsing)
- `start_topic(topic)` - resets state, extracts key concepts, asks the first question
- `evaluate_answer(answer)` - retrieves context, gets structured evaluation, updates concept coverage; suppresses follow-ups once the topic is complete or the limit is reached
- `next_question()` - targets the next uncovered concept
- `progress` / `topic_complete` - used by the UI for the progress bar and mastery detection

**Robust LLM output handling:** `_parse_json()` tolerates markdown code fences, `<think>…</think>` reasoning blocks, and JSON embedded in surrounding prose. If parsing fails entirely, the agent falls back to a neutral "partial" evaluation, so a malformed LLM response never crashes a session.

---

## 5. Prompt Design (`agent/prompts.py`)

All four prompts share two design rules:

1. **Material-only grounding** - every prompt containing retrieved context carries a strict instruction to use *only* that context and never general knowledge. This keeps questions and evaluations faithful to what the lecturer actually taught.
2. **Language matching** - topics, concepts, questions, and feedback are generated in the language of the uploaded material (German slides produce German questions).

| Prompt | Role | Output |
|---|---|---|
| `TOPIC_EXTRACTION_PROMPT` | Find main topics in the sampled document | JSON array of topic names |
| `CONCEPT_EXTRACTION_PROMPT` | Derive the mastery checklist per topic | JSON array of 3-5 concepts |
| `QUESTION_GENERATION_PROMPT` | One focused question (single aspect, < 25 words) about a target concept, avoiding repeats | Plain question text |
| `EVALUATION_PROMPT` | Grade an answer against the context and checklist | JSON: `score`, `feedback`, `follow_up`, `missing`, `covered_concepts` |

### Evaluation JSON schema

```json
{
  "score": "correct | partial | incorrect",
  "feedback": "1-2 encouraging, specific sentences",
  "follow_up": "single-focus question addressing the biggest gap, or null",
  "missing": "the key concept the student missed, or null",
  "covered_concepts": ["names copied exactly from the checklist"]
}
```

---

## 6. Configuration

The app is configured entirely via `.env` (see `.env.example`) and works with any OpenAI-compatible API:

| Variable | Meaning | Example |
|---|---|---|
| `API_KEY` | API key for the LLM endpoint | - |
| `BASE_URL` | OpenAI-compatible base URL | `https://chat.kiconnect.nrw/api/v1` |
| `MODEL` | Model identifier at that endpoint | `OpenAI-GPT-5-Mini` |

> Note: the currently used model rejects non-default `temperature` values, so the agent deliberately sends none.

### Docker deployment

The app can also run as a container (see README for commands). Two build-time decisions keep the image practical:

- **CPU-only PyTorch** - the default Linux `torch` wheel bundles CUDA libraries and would inflate the image by several gigabytes; the Dockerfile installs the CPU build instead, which is all the embedding model needs.
- **Embedding model baked into the image** - `all-MiniLM-L6-v2` is downloaded at build time, so a fresh container answers the first upload immediately instead of downloading the model at runtime.

Credentials are never part of the image: `.env` is excluded via `.dockerignore` and injected at runtime (`env_file` in Compose or `docker run --env-file .env`).

---

## 7. Testing

22 unit tests (`pytest`) cover the deterministic, LLM-independent logic:

- **JSON parsing** - plain JSON, markdown fences, `<think>` blocks, JSON embedded in prose, invalid input
- **Text sampling** - short documents returned whole; long documents sampled from start/middle/end within budget
- **Concept matching** - exact, case-/whitespace-insensitive, unknown names ignored
- **Chunking** - size limits, overlap correctness, blank-line collapsing, empty input
- **Document loading** - TXT decoding, rejection of unsupported file types

```bash
python -m pytest tests/ -v
```

LLM-dependent behavior (question quality, evaluation accuracy) is validated manually through exploratory testing with real lecture materials.

---

## 8. Design Decisions

- **No agent framework (LangChain etc.)** - the pipeline is a small, fixed sequence of LLM calls plus one vector index. Direct use of the OpenAI client and FAISS keeps the code transparent, debuggable, and dependency-light.
- **Material as ground truth** - grounding everything in the uploaded document is what makes the tool useful for exam preparation (it tests *the course*, not the internet).
- **Concept checklist as mastery criterion** - an explicit, inspectable definition of "topic fully explained", visible to the student as a progress bar and checklist, rather than an opaque LLM judgment.
- **Follow-up limit with retry-from-new-angle** - prevents frustrating dead-end loops on a single question while still refusing to skip unmastered concepts.

## 9. Future Work

- Session persistence and per-student progress history
- End-of-session learning summary ("mastered X, review Y") and export
- Multi-document support (whole-course index)
- CI pipeline running the test suite on every push
- Optional OCR for scanned PDFs
