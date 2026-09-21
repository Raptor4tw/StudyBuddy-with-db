"""
auth.py - User signup and login for StudyBuddy (no email verification).

Design:
    - SQLite table `users` (see schema.sql) stores email, username, and a
      bcrypt password hash.
    - Signup creates the user immediately; they can log in right away.

Environment variables (add to .env / .env):
    USERS_DB_PATH=studybuddy_users.db   # optional, defaults as shown
"""

import os
import re
import sqlite3
from contextlib import closing

import bcrypt
import secrets
from datetime import datetime, timedelta, timezone

DB_PATH = os.environ.get("USERS_DB_PATH", "studybuddy_users.db")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# --------------------------------------------------------------------------
# DB helpers
# --------------------------------------------------------------------------

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the users table if it doesn't exist yet. Call once at app startup."""
    with closing(get_connection()) as conn, open(
        os.path.join(os.path.dirname(__file__), "schema.sql")
    ) as f:
        conn.executescript(f.read())
        conn.commit()


# --------------------------------------------------------------------------
# Password hashing
# --------------------------------------------------------------------------

def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(plain_password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))


# --------------------------------------------------------------------------
# Signup
# --------------------------------------------------------------------------

class SignupError(Exception):
    """Raised for any user-facing signup validation failure."""


def create_user(email: str, username: str, password: str) -> dict:
    """
    Create a new user and return {id, email, username} immediately usable for login.
    Raises SignupError with a user-facing message on any validation failure.
    """
    email = email.strip().lower()
    username = username.strip()

    if not EMAIL_RE.match(email):
        raise SignupError("Please enter a valid email address.")
    if len(username) < 3:
        raise SignupError("Username must be at least 3 characters.")
    if len(password) < 8:
        raise SignupError("Password must be at least 8 characters.")

    password_hash = hash_password(password)

    with closing(get_connection()) as conn:
        try:
            cursor = conn.execute(
                "INSERT INTO users (email, username, password_hash) VALUES (?, ?, ?)",
                (email, username, password_hash),
            )
            conn.commit()
            user_id = cursor.lastrowid
        except sqlite3.IntegrityError as e:
            if "email" in str(e):
                raise SignupError("An account with this email already exists.")
            raise SignupError("This username is already taken.")

    return {"id": user_id, "email": email, "username": username}


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------

class LoginError(Exception):
    """Raised for any user-facing login failure."""


def authenticate(email_or_username: str, password: str) -> dict:
    """
    Verify credentials and return {id, email, username} on success.
    Raises LoginError with a user-facing message on failure.
    """
    identifier = email_or_username.strip().lower()

    with closing(get_connection()) as conn:
        row = conn.execute(
            "SELECT id, email, username, password_hash FROM users "
            "WHERE email = ? OR username = ?",
            (identifier, email_or_username.strip()),
        ).fetchone()

    if row is None or not check_password(password, row["password_hash"]):
        raise LoginError("Incorrect email/username or password.")

    return {"id": row["id"], "email": row["email"], "username": row["username"]}

SESSION_EXPIRY_DAYS = int(os.environ.get("SESSION_EXPIRY_DAYS", "30"))


def create_session(user_id: int) -> str:
    """Create a persistent login session and return its token."""
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=SESSION_EXPIRY_DAYS)).isoformat()
    with closing(get_connection()) as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires_at),
        )
        conn.commit()
    return token


def get_user_by_session(token: str) -> dict | None:
    """Return {id, email, username} if the session token is valid and not expired, else None."""
    if not token:
        return None
    with closing(get_connection()) as conn:
        row = conn.execute(
            """SELECT u.id, u.email, u.username, s.expires_at
               FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token = ?""",
            (token,),
        ).fetchone()

        if row is None:
            return None
        if datetime.now(timezone.utc) > datetime.fromisoformat(row["expires_at"]):
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            conn.commit()
            return None

    return {"id": row["id"], "email": row["email"], "username": row["username"]}


def delete_session(token: str) -> None:
    if not token:
        return
    with closing(get_connection()) as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()