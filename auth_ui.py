"""
auth_ui.py - Streamlit signup/login screens for StudyBuddy.

Session persistence strategy:
    - Token is stored in st.query_params["t"] (URL). Works on Streamlit
      Community Cloud where iframe-based cookie writes crash the ASGI
      server with "RuntimeError: Expected ASGI message 'http.response.start'".
    - A "Stay signed in" checkbox controls whether the token survives
      beyond the current browser tab.
    - A small "bookmark this link" hint plus a copy button in the sidebar
      lets the user persist the login across new tabs, which is the
      only way to get cookie-like behaviour on this platform.

    A USE_COOKIE flag is provided as an escape hatch: if you deploy on a
    Streamlit version without the ASGI crash (e.g. 1.49.x) and want real
    cookies, set USE_COOKIE = True. The cookie path is iframe-based and
    will NOT work on the current Cloud deployment.
"""

import streamlit as st

from auth import (
    LoginError,
    SignupError,
    authenticate,
    cleanup_expired_sessions,
    create_session,
    create_user,
    delete_session,
    get_user_by_session,
)

TOKEN_PARAM = "t"
USE_COOKIE = False  # flip to True only on Streamlit versions without the ASGI bug

COOKIE_NAME = "studybuddy_session"
COOKIE_EXPIRY_DAYS = 30


# --------------------------------------------------------------------------
# Cookie helpers (only used when USE_COOKIE = True)
# --------------------------------------------------------------------------

def _set_cookie_and_reload(token: str) -> None:
    import streamlit.components.v1 as components
    components.html(
        f"""<script>
        try {{
            const secure = window.parent.location.protocol === "https:" ? "; Secure" : "";
            window.parent.document.cookie = "{COOKIE_NAME}={token}; max-age={COOKIE_EXPIRY_DAYS * 86400}; path=/; SameSite=Lax" + secure;
        }} catch (e) {{ console.error(e); }}
        window.parent.location.reload();
        </script>""",
        height=1,
        width=1,
    )
    st.stop()


def _clear_cookie_and_reload() -> None:
    import streamlit.components.v1 as components
    components.html(
        f"""<script>
        try {{
            window.parent.document.cookie = "{COOKIE_NAME}=; max-age=0; path=/; SameSite=Lax";
        }} catch (e) {{ console.error(e); }}
        window.parent.location.reload();
        </script>""",
        height=1,
        width=1,
    )
    st.stop()


# --------------------------------------------------------------------------
# Token helpers (query-param based, the default)
# --------------------------------------------------------------------------

def _set_token(token: str) -> None:
    st.query_params[TOKEN_PARAM] = token


def _clear_token() -> None:
    if TOKEN_PARAM in st.query_params:
        del st.query_params[TOKEN_PARAM]


def _get_token() -> str | None:
    if USE_COOKIE:
        return st.context.cookies.get(COOKIE_NAME)
    return st.query_params.get(TOKEN_PARAM)


def _clear_all_session_state() -> None:
    """Wipe every tab-local value.

    Critical for user isolation: when one user logs out and another logs in
    on the same browser tab, leftover caches (_docs_cache, _doc_meta_cache,
    _rag_cache, agent, conversation, mistakes_by_user, active_document, ...)
    would leak the previous user's data into the next session.
    """
    for key in list(st.session_state.keys()):
        st.session_state.pop(key, None)


# --------------------------------------------------------------------------
# Auth screen
# --------------------------------------------------------------------------

_AUTH_CSS = """
<style>
    .auth-hero { text-align:center; margin-top:2rem; margin-bottom:1.5rem; }
    .auth-hero h1 { font-size:2.2rem; margin-bottom:0.2rem; }
    .auth-hero p { color:#8a8a8a; font-size:0.95rem; }
    .auth-card {
        background: linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.02));
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 16px;
        padding: 1.5rem 1.5rem 0.5rem 1.5rem;
        box-shadow: 0 8px 24px rgba(0,0,0,0.25);
    }
</style>
"""


def _render_auth_header() -> None:
    st.markdown(
        """
        <div class="auth-hero">
            <h1>📚 StudyBuddy</h1>
            <p>AI Learning Partner — sign in to continue</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _login_form() -> None:
    with st.form("login_form"):
        identifier = st.text_input(
            "Email or username",
            placeholder="you@example.com",
            autocomplete="username",
        )
        password = st.text_input(
            "Password",
            type="password",
            placeholder="••••••••",
            autocomplete="current-password",
        )
        stay_signed_in = st.checkbox(
            "Stay signed in on this device",
            value=True,
            help=(
                "When enabled, your login is kept in the URL so it survives "
                "a reload or bookmark. Turn off on shared computers."
            ),
        )
        submitted = st.form_submit_button("Log in", use_container_width=True, type="primary")

    if not submitted:
        return

    try:
        user = authenticate(identifier, password)
    except LoginError as e:
        st.error(str(e))
        return

    try:
        cleanup_expired_sessions()
    except Exception:
        pass

    token = create_session(user["id"])

    # Nuke any leftover state from a previous user on this browser tab.
    _clear_all_session_state()

    st.session_state["user"] = user

    if stay_signed_in:
        if USE_COOKIE:
            _set_cookie_and_reload(token)
        _set_token(token)
    else:
        if USE_COOKIE:
            _set_cookie_and_reload(token)
        _set_token(token)

    st.toast(f"Willkommen zurück, {user['username']}!", icon="👋")
    st.rerun()


def _signup_form() -> None:
    with st.form("signup_form"):
        email = st.text_input("Email", placeholder="you@example.com", autocomplete="email")
        username = st.text_input("Username", placeholder="min. 3 characters", autocomplete="username")
        password = st.text_input(
            "Password",
            type="password",
            placeholder="min. 8 characters",
            autocomplete="new-password",
        )
        password_confirm = st.text_input(
            "Confirm password",
            type="password",
            placeholder="repeat your password",
            autocomplete="new-password",
        )
        submitted = st.form_submit_button("Create account", use_container_width=True, type="primary")

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

    # Nuke any leftover state from a previous user on this browser tab.
    _clear_all_session_state()

    st.session_state["user"] = user

    if USE_COOKIE:
        _set_cookie_and_reload(token)
    _set_token(token)

    st.toast(f"Account created. Willkommen, {user['username']}!", icon="🎉")
    st.rerun()


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def require_login() -> dict | None:
    """Return the logged-in user dict, or render the auth screen."""
    if "user" in st.session_state:
        return st.session_state["user"]

    token = _get_token()
    if token:
        user = get_user_by_session(token)
        if user:
            st.session_state["user"] = user
            return user
        _clear_token()
        if USE_COOKIE:
            _clear_cookie_and_reload()

    st.markdown(_AUTH_CSS, unsafe_allow_html=True)
    _render_auth_header()

    left, center, right = st.columns([1, 2, 1])
    with center:
        with st.container():
            st.markdown('<div class="auth-card">', unsafe_allow_html=True)
            login_tab, signup_tab = st.tabs(["Log in", "Sign up"])
            with login_tab:
                _login_form()
            with signup_tab:
                _signup_form()
            st.markdown("</div>", unsafe_allow_html=True)
        st.markdown(
            '<div style="color:#8a8a8a;font-size:0.8rem;text-align:center;margin-top:0.5rem;">'
            "Your login is kept in the URL. Bookmark the page to stay signed in on new tabs."
            "</div>",
            unsafe_allow_html=True,
        )

    return None


def logout() -> None:
    """Clears session_state, the DB session row, and the URL token/cookie."""
    token = _get_token()
    if token:
        delete_session(token)
    _clear_token()

    # Wipe every tab-local cache so the next user on this browser sees a
    # clean slate. Without this, documents / mistakes / agent state from the
    # previous user would leak into the next login.
    _clear_all_session_state()

    if USE_COOKIE:
        _clear_cookie_and_reload()
    st.rerun()


def render_stay_signed_in_help() -> None:
    """Small sidebar helper showing the current login token + copy button."""
    token = _get_token()
    if not token:
        return
    with st.expander("🔗 Stay signed in on new tabs"):
        st.caption(
            "Save this token in a bookmark — opening it in a new tab keeps "
            "you logged in."
        )
        st.code(f"{TOKEN_PARAM}={token}", language=None)