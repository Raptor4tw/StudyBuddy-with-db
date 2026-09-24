-- Users table
CREATE TABLE IF NOT EXISTS users (
    id             SERIAL PRIMARY KEY,
    email          TEXT NOT NULL UNIQUE,
    username       TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
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