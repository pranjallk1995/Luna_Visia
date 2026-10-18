"""Hidden Images tab: password-gated access to the Hidden Vault gallery."""

import httpx
import streamlit as st

from services.api import LunaVisiaAPI
from services.images import ImageCollection
from services.state import SessionState
from views.actions import HiddenActions
from views.gallery import ImageGallery
from views.shared import SearchFilter, VisiaTab


class HiddenAccessGate:
    """Create, unlock, or reset Hidden Vault access and store the session token."""

    def __init__(self, api: LunaVisiaAPI) -> None:
        self.api = api

    def unlocked(self) -> bool:
        """Return True when a session exists; otherwise render the access forms."""
        configured = self.api.hidden_status()
        if SessionState.hidden_token():
            return True
        if configured:
            self._render_login()
            self._render_reset()
        else:
            self._render_setup()
        return False

    @staticmethod
    def _unlock(token: str) -> None:
        SessionState.set_hidden_token(token)
        st.rerun()

    def _render_setup(self) -> None:
        st.subheader("Create hidden-vault access")
        st.caption("Choose a password and an 8-digit reset PIN. Store the PIN safely.")
        with st.form("hidden_setup"):
            password = st.text_input("Create password", type="password")
            confirm = st.text_input("Confirm password", type="password")
            pin = st.text_input("Create reset PIN", type="password", max_chars=8)
            submitted = st.form_submit_button("Create and unlock", width="stretch")
        if not submitted:
            return
        if password != confirm:
            st.error("Passwords do not match.")
            return
        try:
            self._unlock(self.api.hidden_setup(password, pin))
        except httpx.HTTPStatusError as error:
            st.error(self.api.error_message(error, "Could not create access."))

    def _render_login(self) -> None:
        st.subheader("Unlock hidden images")
        with st.form("hidden_login"):
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Unlock", width="stretch")
        if not submitted:
            return
        try:
            self._unlock(self.api.hidden_login(password))
        except httpx.HTTPStatusError:
            st.error("Incorrect password.")

    def _render_reset(self) -> None:
        with st.expander("Reset password"):
            with st.form("hidden_reset"):
                pin = st.text_input("8-digit reset PIN", type="password", max_chars=8)
                new_password = st.text_input("New password", type="password")
                confirm = st.text_input("Confirm new password", type="password")
                submitted = st.form_submit_button("Reset and unlock", width="stretch")
            if not submitted:
                return
            if new_password != confirm:
                st.error("Passwords do not match.")
                return
            try:
                self._unlock(self.api.hidden_reset(pin, new_password))
            except httpx.HTTPStatusError as error:
                st.error(self.api.error_message(error, "Password reset failed."))


class HiddenTab(VisiaTab):
    def render(self) -> None:
        if not HiddenAccessGate(self.api).unlocked():
            return
        self._render_lock_bar()
        collection = SearchFilter(
            self.layout,
            key="hidden_name_search",
            label="Search hidden vault by image name",
            no_match_message="No hidden filenames or tags match your search.",
        ).apply(ImageCollection.from_metadata(self.files, self._hidden_metadata()))
        if collection is not None:
            ImageGallery(self.context, HiddenActions(self.api)).render(collection)

    def _render_lock_bar(self) -> None:
        status_column, lock_column = st.columns([3, 1])
        status_column.success("Hidden vault unlocked")
        if lock_column.button("Lock", icon=":material/lock:", width="stretch"):
            try:
                self.api.hidden_logout(SessionState.hidden_token())
            finally:
                SessionState.set_hidden_token(None)
                st.rerun()

    def _hidden_metadata(self) -> list[dict]:
        """Load hidden metadata, dropping an expired session back to the gate."""
        try:
            return self.api.hidden_images(SessionState.hidden_token())
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 401:
                SessionState.set_hidden_token(None)
                st.rerun()
            raise
