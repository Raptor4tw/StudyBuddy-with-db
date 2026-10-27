-- Users table
CREATE TABLE IF NOT EXISTS users (
    id             SERIAL PRIMARY KEY,
    email          TEXT NOT NULL UNIQUE,
    username       TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    preferred_model TEXT,
    created_at     TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Persistent login sessions (cookie-backed)
CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    expires_at  TIMESTAMP NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- Persisted study sessions (per-user chat/topic history)
CREATE TABLE IF NOT EXISTS study_sessions (
    id                SERIAL PRIMARY KEY,
    user_id           INTEGER NOT NULL REFERENCES users(id),
    document_name     TEXT NOT NULL,
    document_text     TEXT NOT NULL,
    topic             TEXT NOT NULL,
    topics_json       TEXT,
    concepts_json     TEXT NOT NULL,
    conversation_json TEXT NOT NULL,
    mistakes_json     TEXT NOT NULL,
    current_concept   TEXT,
    current_question  TEXT,
    follow_up_count   INTEGER NOT NULL DEFAULT 0,
    created_at        TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS hidden_topics (
    id             SERIAL PRIMARY KEY,
    user_id        INTEGER NOT NULL REFERENCES users(id),
    document_name  TEXT NOT NULL,
    topic          TEXT NOT NULL,
    created_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, document_name, topic)
);

CREATE INDEX IF NOT EXISTS idx_hidden_topics_user_doc ON hidden_topics(user_id, document_name);

CREATE INDEX IF NOT EXISTS idx_study_sessions_user ON study_sessions(user_id);


-- One row per LLM call, so token usage survives logout and resume
CREATE TABLE IF NOT EXISTS llm_usage (
    id                SERIAL PRIMARY KEY,
    user_id           INTEGER NOT NULL REFERENCES users(id),
    session_id        INTEGER REFERENCES study_sessions(id) ON DELETE CASCADE,
    model             TEXT NOT NULL,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    created_at        TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_user ON llm_usage(user_id);
CREATE INDEX IF NOT EXISTS idx_llm_usage_session ON llm_usage(session_id);
CREATE INDEX IF NOT EXISTS idx_llm_usage_user_time ON llm_usage(user_id, created_at);
