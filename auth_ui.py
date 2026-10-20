"""
auth_ui.py - Streamlit signup/login screens for StudyBuddy, with a
persistent login cookie so sessions survive new tabs and reloads.

Cookie handling:
    - READ via st.context.cookies (built into Streamlit, no extra package,
      available immediately at script start from the request headers).
    - WRITE via a tiny injected <script> that sets the cookie on the PARENT
      document (since components.html renders inside its own iframe), then
      triggers a full browser reload itself. The reload is triggered by the
      script, not by st.rerun(), so the cookie write is guaranteed to finish
      before anything else happens — doing it the other way around created a
      race where Streamlit tore the iframe down before the script could run.
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


def _set_cookie_and_reload(token: str) -> None:
    components.html(
        f"""<script>
        try {{
            const secure = window.parent.location.protocol === "https:" ? "; Secure" : "";
            window.parent.document.cookie = "{COOKIE_NAME}={token}; max-age={COOKIE_EXPIRY_DAYS * 86400}; path=/; SameSite=Lax" + secure;
        }} catch (e) {{
            console.error("Could not set cookie on parent document:", e);
        }}
        window.parent.location.reload();
        </script>""",
        height=0,
        width=0,
    )


def _clear_cookie_and_reload() -> None:
    components.html(
        f"""<script>
        try {{
            window.parent.document.cookie = "{COOKIE_NAME}=; max-age=0; path=/; SameSite=Lax";
        }} catch (e) {{
            console.error("Could not clear cookie on parent document:", e);
        }}
        window.parent.location.reload();
        </script>""",
        height=0,
        width=0,
    )


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
    st.info("Logging in…")
    _set_cookie_and_reload(token)
    st.stop()


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
    st.success(f"Account created! Welcome, {user['username']}. Logging in…")
    _set_cookie_and_reload(token)
    st.stop()


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
        _clear_cookie_and_reload()
        st.stop()

    st.title("StudyBuddy — Sign in")
    login_tab, signup_tab = st.tabs(["Log in", "Sign up"])

    with login_tab:
        _login_form()

    with signup_tab:
        _signup_form()

    return None


def logout() -> None:
    """Clears session_state, the DB session row, and the browser cookie, then reloads."""
    token = st.context.cookies.get(COOKIE_NAME)
    if token:
        delete_session(token)
    st.session_state.pop("user", None)
    _clear_cookie_and_reload()
    st.stop()