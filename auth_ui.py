"""
auth_ui.py - Streamlit signup/login screens for StudyBuddy, with a
persistent login session carried via a URL query parameter instead of
a cookie.

Why a query param instead of a cookie: this Streamlit Cloud environment
has a server-side bug where any iframe-based content (components.html,
st.iframe) crashes with an ASGI RuntimeError, which made cookie-writing
via injected JavaScript unusable. A query param needs no iframe, no
JavaScript, and no static file serving — it's set and read in pure Python.

Trade-off: the token is visible in the browser's address bar and history.
This is less secure than an HttpOnly cookie, but survives page reloads
and "open in new tab" as long as the URL (with its ?t=... parameter) is
preserved, which Streamlit does automatically across reruns.
"""

import streamlit as st

from auth import (
    LoginError,
    SignupError,
    authenticate,
    create_session,
    create_user,
    delete_session,
    get_user_by_session,
)

TOKEN_PARAM = "t"


def _login_form() -> None:
    with st.form("login_form"):
        identifier = st.text_input("Email or username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")

    if not submitted:
        return

    try:
        user = authenticate(identifier, password)
    except LoginError as e:
        st.error(str(e))
        return

    token = create_session(user["id"])
    st.session_state["user"] = user
    st.query_params[TOKEN_PARAM] = token
    st.rerun()


def _signup_form() -> None:
    with st.form("signup_form"):
        email = st.text_input("Email")
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        password_confirm = st.text_input("Confirm password", type="password")
        submitted = st.form_submit_button("Sign up")

    if not submitted:
        return

    if password != password_confirm:
        st.error("Passwords do not match.")
        return

    try:
        user = create_user(email, username, password)
    except SignupError as e:
        st.error(str(e))
        return

    token = create_session(user["id"])
    st.session_state["user"] = user
    st.query_params[TOKEN_PARAM] = token
    st.rerun()


def require_login() -> dict | None:
    """
    Returns the logged-in user dict, checking: session_state (this tab's
    current connection), then the token in the URL query params (survives
    reloads and new tabs as long as the URL is carried along), then falls
    back to rendering login/signup tabs.
    """
    if "user" in st.session_state:
        return st.session_state["user"]

    token = st.query_params.get(TOKEN_PARAM)
    if token:
        user = get_user_by_session(token)
        if user:
            st.session_state["user"] = user
            return user
        # stale/expired token — drop it from the URL
        del st.query_params[TOKEN_PARAM]

    st.title("StudyBuddy — Sign in")
    login_tab, signup_tab = st.tabs(["Log in", "Sign up"])

    with login_tab:
        _login_form()

    with signup_tab:
        _signup_form()

    return None


def logout() -> None:
    """Clears session_state, the DB session row, and the URL token."""
    token = st.query_params.get(TOKEN_PARAM)
    if token:
        delete_session(token)
        del st.query_params[TOKEN_PARAM]
    st.session_state.pop("user", None)
    st.rerun()