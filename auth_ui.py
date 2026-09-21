"""
auth_ui.py - Streamlit signup/login screens for StudyBuddy, with a
persistent login cookie so sessions survive new tabs and reloads.

Cookie handling:
    - READ via st.context.cookies (built into Streamlit, no extra package,
      available immediately at script start from the request headers).
    - WRITE via a tiny injected <script> that sets document.cookie directly.
      Takes effect on the *next* page load / new tab, since Streamlit only
      sees cookies that were present in the original HTTP request.
"""

import streamlit as st
import streamlit.components.v1 as components

from auth import (
    LoginError,
    SignupError,
    authenticate,
    create_session,
    create_user,
    delete_session,
    get_user_by_session,
)

COOKIE_NAME = "studybuddy_session"
COOKIE_EXPIRY_DAYS = 30


def _set_cookie(token: str) -> None:
    components.html(
        f"""<script>
        document.cookie = "{COOKIE_NAME}={token}; max-age={COOKIE_EXPIRY_DAYS * 86400}; path=/; SameSite=Lax";
        </script>""",
        height=0,
        width=0,
    )


def _clear_cookie() -> None:
    components.html(
        f"""<script>
        document.cookie = "{COOKIE_NAME}=; max-age=0; path=/; SameSite=Lax";
        </script>""",
        height=0,
        width=0,
    )


def _login_form() -> dict | None:
    with st.form("login_form"):
        identifier = st.text_input("Email or username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")

    if not submitted:
        return None

    try:
        user = authenticate(identifier, password)
    except LoginError as e:
        st.error(str(e))
        return None

    token = create_session(user["id"])
    _set_cookie(token)
    return user


def _signup_form() -> dict | None:
    with st.form("signup_form"):
        email = st.text_input("Email")
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        password_confirm = st.text_input("Confirm password", type="password")
        submitted = st.form_submit_button("Sign up")

    if not submitted:
        return None

    if password != password_confirm:
        st.error("Passwords do not match.")
        return None

    try:
        user = create_user(email, username, password)
    except SignupError as e:
        st.error(str(e))
        return None

    token = create_session(user["id"])
    _set_cookie(token)
    st.success(f"Account created! Welcome, {user['username']}.")
    return user


def require_login() -> dict | None:
    """
    Returns the logged-in user dict, checking: session_state (this tab's
    current connection), then the login cookie (survives new tabs/reloads),
    then falls back to rendering login/signup tabs.
    """
    if "user" in st.session_state:
        return st.session_state["user"]

    token = st.context.cookies.get(COOKIE_NAME)
    if token:
        user = get_user_by_session(token)
        if user:
            st.session_state["user"] = user
            return user
        # stale/expired token — clean it up client-side
        _clear_cookie()

    st.title("StudyBuddy — Sign in")
    login_tab, signup_tab = st.tabs(["Log in", "Sign up"])

    with login_tab:
        user = _login_form()
        if user:
            st.session_state["user"] = user
            st.rerun()

    with signup_tab:
        user = _signup_form()
        if user:
            st.session_state["user"] = user
            st.rerun()

    return None


def logout() -> None:
    """Clears session_state, the DB session row, and the browser cookie."""
    token = st.context.cookies.get(COOKIE_NAME)
    if token:
        delete_session(token)
    _clear_cookie()
    st.session_state.pop("user", None)