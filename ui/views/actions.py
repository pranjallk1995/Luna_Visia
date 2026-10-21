"""Per-collection image actions used by the shared gallery."""

from collections.abc import Callable
from dataclasses import dataclass

import streamlit as st

from services.api import LunaVisiaAPI
from services.state import SessionState


@dataclass(frozen=True)
class MenuAction:
    """An extra icon button in an image's overflow menu."""

    icon: str
    key: str
    run: Callable[[str], None]


class GalleryActions:
    """What a gallery may do with its images; visible images by default."""

    shows_favorites = True
    allows_hiding = True
    allows_renaming = True

    def __init__(self, api: LunaVisiaAPI) -> None:
        self.api = api

    def menu_actions(self) -> list[MenuAction]:
        """Buttons shown after the shared download and delete actions."""
        return [MenuAction(":material/visibility_off:", "hide", SessionState.request_hide)]

    def favorite_names(self) -> set[str]:
        return {item["name"] for item in self.api.favorites()}

    def set_favorite(self, name: str, favorite: bool) -> None:
        self.api.set_favorites([name], favorite)
        SessionState.refresh_gallery()

    def delete(self, name: str) -> None:
        self.api.delete_image(name)
        SessionState.forget_upload(name)
        self._after_delete()

    def hide(self, name: str) -> None:
        self.api.hide_images([name])
        st.session_state.pending_view_hide = None
        SessionState.refresh_gallery()

    def rename(self, name: str, new_name: str) -> None:
        self.api.rename_image(name, new_name)
        SessionState.refresh_gallery()

    def update_metadata(self, name: str, **change) -> dict:
        return self.api.update_image_metadata(name, self.api.metadata_change(**change))

    @staticmethod
    def _after_delete() -> None:
        st.session_state.pending_view_delete = None
        SessionState.refresh_gallery()


class FavoriteActions(GalleryActions):
    """Favorites expose the same actions as View Images, without hiding."""

    allows_hiding = False

    def menu_actions(self) -> list[MenuAction]:
        return []


class HiddenActions(GalleryActions):
    """Hidden-image actions for the OAuth-authenticated application user."""

    shows_favorites = False
    allows_hiding = False
    allows_renaming = False

    def menu_actions(self) -> list[MenuAction]:
        return [MenuAction(":material/visibility:", "unhide", self.restore)]

    def favorite_names(self) -> set[str]:
        return set()

    def restore(self, name: str) -> None:
        self.api.restore_hidden_images([name])
        SessionState.refresh_gallery()

    def delete(self, name: str) -> None:
        self.api.delete_hidden_images([name])
        self._after_delete()

    def update_metadata(self, name: str, **change) -> dict:
        return self.api.update_hidden_metadata(name, self.api.metadata_change(**change))
