"""
auth.py - User signup, login, and persistent login sessions for StudyBuddy,
backed by Postgres (Supabase) instead of local SQLite.
"""

import os
import re
import secrets
from contextlib import closing
from datetime import datetime, timedelta, timezone

import bcrypt
import psycopg2
from psycopg2.extras import RealDictCursor

DB_URL = os.environ["SUPABASE_DB_URL"]
SESSION_EXPIRY_DAYS = int(os.environ.get("SESSION_EXPIRY_DAYS", "30"))
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_DUMMY_HASH = bcrypt.hashpw(b"dummy-password-for-timing", bcrypt.gensalt()).decode("utf-8")


def get_connection():
    return psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)


def init_db() -> None:
    with closing(get_connection()) as conn, open(
        os.path.join(os.path.dirname(__file__), "schema.sql")
    ) as f:
        with conn.cursor() as cur:
            cur.execute(f.read())
        conn.commit()


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(plain_password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))


class SignupError(Exception):
    pass


def create_user(email: str, username: str, password: str) -> dict:
    email = email.strip().lower()
    username = username.strip()

    if not EMAIL_RE.match(email):
        raise SignupError("Please enter a valid email address.")
    if len(username) < 3:
        raise SignupError("Username must be at least 3 characters.")
    if len(username) > 40:
        raise SignupError("Username must be at most 40 characters.")
    if len(password) < 8:
        raise SignupError("Password must be at least 8 characters.")

    password_hash = hash_password(password)

    with closing(get_connection()) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (email, username, password_hash) VALUES (%s, %s, %s) RETURNING id",
                    (email, username, password_hash),
                )
                user_id = cur.fetchone()["id"]
            conn.commit()
        except psycopg2.errors.UniqueViolation as e:
            conn.rollback()
            constraint = getattr(e.diag, "constraint_name", "") or ""
            if "email" in constraint:
                raise SignupError("An account with this email already exists.")
            raise SignupError("This username is already taken.")

    return {"id": user_id, "email": email, "username": username}


class LoginError(Exception):
    pass


def authenticate(email_or_username: str, password: str) -> dict:
    raw = email_or_username.strip()
    identifier = raw.lower()

    with closing(get_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, email, username, password_hash FROM users "
                "WHERE email = %s OR username = %s",
                (identifier, raw),
            )
            row = cur.fetchone()

    password_hash = row["password_hash"] if row else _DUMMY_HASH
    ok = check_password(password, password_hash)

    if row is None or not ok:
        raise LoginError("Incorrect email/username or password.")

    return {"id": row["id"], "email": row["email"], "username": row["username"]}


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_EXPIRY_DAYS)
    with closing(get_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sessions (token, user_id, expires_at) VALUES (%s, %s, %s)",
                (token, user_id, expires_at),
            )
        conn.commit()
    return token


def get_user_by_session(token: str) -> dict | None:
    if not token:
        return None
    with closing(get_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT u.id, u.email, u.username, s.expires_at
                   FROM sessions s JOIN users u ON u.id = s.user_id
                   WHERE s.token = %s""",
                (token,),
            )
            row = cur.fetchone()

            if row is None:
                return None

            expires_at = row["expires_at"]
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

            if datetime.now(timezone.utc) > expires_at:
                cur.execute("DELETE FROM sessions WHERE token = %s", (token,))
                conn.commit()
                return None

    return {"id": row["id"], "email": row["email"], "username": row["username"]}


def delete_session(token: str) -> None:
    if not token:
        return
    with closing(get_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE token = %s", (token,))
        conn.commit()


def cleanup_expired_sessions() -> int:
    with closing(get_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE expires_at < NOW()")
            removed = cur.rowcount
        conn.commit()
    return removed