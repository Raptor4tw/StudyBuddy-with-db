"""DB tests for token usage and the preferred model, on a throwaway SQLite file."""
import sqlite3

import pytest

import auth
import history


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(auth, "DB_PATH", path)
    monkeypatch.setattr(history, "DB_PATH", path)
    auth.init_db()
    return path


def _user(email="a@example.invalid", name="alice"):
    return auth.create_user(email, name, "pw-123456789")


def _session(user_id):
    return history.create_session(user_id, "doc.txt", "text", "Topic", {}, [], [], None, None, 0)


def test_usage_summed_per_model_and_session(db):
    u = _user()
    s1, s2 = _session(u["id"]), _session(u["id"])
    history.record_usage(u["id"], None, [{"model": "A", "prompt_tokens": 5, "completion_tokens": 5}])
    history.record_usage(u["id"], s1, [
        {"model": "A", "prompt_tokens": 100, "completion_tokens": 10},
        {"model": "B", "prompt_tokens": 50, "completion_tokens": 5},
        {"model": "A", "prompt_tokens": 1, "completion_tokens": 2},
    ])
    history.record_usage(u["id"], s2, [{"model": "B", "prompt_tokens": 9, "completion_tokens": 9}])
    assert history.usage_by_model(u["id"], s1) == {"A": (101, 12, 100), "B": (50, 5, 50)}


def test_usage_is_private_per_user(db):
    u, other = _user(), _user("b@example.invalid", "bob")
    s = _session(u["id"])
    history.record_usage(u["id"], s, [{"model": "A", "prompt_tokens": 1, "completion_tokens": 1}])
    assert history.usage_by_model(other["id"], s) == {}


def test_deleting_session_deletes_its_usage(db):
    u = _user()
    s = _session(u["id"])
    history.record_usage(u["id"], s, [{"model": "A", "prompt_tokens": 1, "completion_tokens": 1}])
    history.delete_session(s, u["id"])
    assert history.usage_by_model(u["id"], s) == {}


def test_preferred_model_round_trip(db):
    u = _user()
    assert history.get_preferred_model(u["id"]) is None
    history.set_preferred_model(u["id"], "Mistral Small 4 119B")
    assert history.get_preferred_model(u["id"]) == "Mistral Small 4 119B"


def test_init_db_migrates_old_users_table(tmp_path, monkeypatch):
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE, username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')))""")
    monkeypatch.setattr(auth, "DB_PATH", path)
    auth.init_db()
    auth.init_db()  # second start must not try to add the column again
    with sqlite3.connect(path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    assert "preferred_model" in cols
