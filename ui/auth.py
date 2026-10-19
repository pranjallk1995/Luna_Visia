"""OIDC authentication and account authorization for Luna Visia."""

import base64
from collections.abc import Mapping

import streamlit as st


class OIDCAuthentication:
    """Gate the application behind Streamlit's OIDC identity cookie."""

    GOOGLE_LOGO = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
<path fill="#4285F4" d="M21.6 12.23c0-.71-.06-1.4-.18-2.07H12v3.92h5.38a4.6 4.6 0 0 1-2 3.02v2.54h3.24c1.9-1.75 2.98-4.33 2.98-7.41z"/>
<path fill="#34A853" d="M12 22c2.7 0 4.97-.9 6.62-2.43l-3.24-2.54c-.9.6-2.05.96-3.38.96-2.6 0-4.81-1.76-5.6-4.13H3.06v2.62A10 10 0 0 0 12 22z"/>
<path fill="#FBBC05" d="M6.4 13.86A6 6 0 0 1 6.08 12c0-.65.11-1.28.32-1.86V7.52H3.06A10 10 0 0 0 2 12c0 1.61.39 3.14 1.06 4.48l3.34-2.62z"/>
<path fill="#EA4335" d="M12 6.01c1.47 0 2.79.5 3.83 1.5l2.87-2.87A9.63 9.63 0 0 0 12 2a10 10 0 0 0-8.94 5.52l3.34 2.62C7.19 7.77 9.4 6.01 12 6.01z"/>
</svg>"""

    def __init__(self, allowed_emails: frozenset[str]) -> None:
        self.allowed_emails = allowed_emails

    @classmethod
    def render_login_style(cls) -> None:
        logo = base64.b64encode(cls.GOOGLE_LOGO.encode()).decode("ascii")
        st.markdown(
            f"""<style>
.st-key-google_login button {{display:inline-flex;align-items:center;justify-content:center;gap:.65rem;border:1px solid var(--lv-border)!important;background:var(--lv-control)!important;color:var(--lv-text)!important}}
.st-key-google_login button:hover {{border-color:var(--lv-strong)!important;background:var(--lv-control-hover)!important;color:var(--lv-text)!important}}
.st-key-google_login button p,.st-key-google_login button span {{color:var(--lv-text)!important;-webkit-text-fill-color:var(--lv-text)!important}}
.st-key-google_login button::before {{content:"";width:1.25rem;height:1.25rem;flex:0 0 1.25rem;background:url("data:image/svg+xml;base64,{logo}") center/contain no-repeat}}
</style>""",
            unsafe_allow_html=True,
        )

    def is_allowed(self, user: Mapping[str, object]) -> bool:
        email = str(user.get("email", "")).strip().casefold()
        return bool(email and email in self.allowed_emails and user.get("email_verified") is True)

    def require_login(self, app_name: str, subtitle: str) -> None:
        if not st.user.is_logged_in:
            st.title(app_name)
            st.caption(subtitle)
            self.render_login_style()
            st.button("Continue with Google", type="primary", key="google_login", on_click=st.login)
            st.stop()
        if not self.is_allowed(st.user):
            st.error("This Google account is not authorized to access this application.")
            st.button("Sign out", on_click=st.logout)
            st.stop()
