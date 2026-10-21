"""Full-size image dialog with inline name, caption, and tag editing."""

import html
from dataclasses import dataclass
from pathlib import Path

import httpx
import streamlit as st

from config import UIConfig
from services.api import LunaVisiaAPI
from services.images import ImageFiles
from views.actions import GalleryActions


@dataclass(frozen=True)
class PreviewKeys:
    """Session-state and widget keys for one image's preview dialog."""

    name: str

    @property
    def name_editing(self) -> str:
        return f"preview_name_edit_{self.name}"

    @property
    def name_draft(self) -> str:
        return f"{self.name_editing}_draft"

    @property
    def name_error(self) -> str:
        return f"{self.name_editing}_error"

    @property
    def renamed(self) -> str:
        return f"{self.name_editing}_done"

    @property
    def caption_editing(self) -> str:
        return f"preview_caption_edit_{self.name}"

    @property
    def caption_draft(self) -> str:
        return f"{self.caption_editing}_draft"

    @property
    def caption_error(self) -> str:
        return f"{self.caption_editing}_error"

    @property
    def tag_adding(self) -> str:
        return f"preview_tag_add_{self.name}"

    @property
    def tag_draft(self) -> str:
        return f"{self.tag_adding}_draft"

    @property
    def tag_error(self) -> str:
        return f"{self.tag_adding}_error"

    @property
    def pills_generation(self) -> str:
        return f"preview_tag_pills_generation_{self.name}"

    @property
    def pills(self) -> str:
        """Tag pills key; its generation suffix remounts the pills after edits."""
        return f"preview_tag_pills_{self.name}_{st.session_state[self.pills_generation]}"


class ImagePreviewDialog:
    """Show one image full size and edit its name, caption, and tags in place.

    Caption and tag edits update ``metadata_by_name`` so the dialog's own
    fragment reruns show the saved values without reloading the page. A rename
    moves the file, so it closes the dialog and reruns the whole app instead.
    """

    def __init__(
        self, files: ImageFiles, actions: GalleryActions, metadata_by_name: dict[str, dict]
    ) -> None:
        self.files = files
        self.actions = actions
        self.metadata_by_name = metadata_by_name

    def open(self, image_path: Path) -> None:
        @st.dialog(self.files.display_name(image_path.name), width="large")
        def show_full_size_image() -> None:
            self._render(image_path)

        show_full_size_image()

    @staticmethod
    def _marker(container, css_class: str) -> None:
        container.markdown(f'<span class="{css_class}"></span>', unsafe_allow_html=True)

    def _save(self, name: str, **change) -> None:
        self.metadata_by_name[name] = self.actions.update_metadata(name, **change)

    def _render(self, image_path: Path) -> None:
        keys = PreviewKeys(image_path.name)
        if st.session_state.pop(keys.renamed, None):
            st.rerun()
        st.image(self.files.full_preview(image_path), width="stretch")
        metadata = self.metadata_by_name.get(image_path.name) or {}
        st.session_state.setdefault(keys.pills_generation, 0)
        if self.actions.allows_renaming:
            self._render_name(keys, image_path)
        self._render_caption(keys, str(metadata.get("caption") or ""))
        self._render_tags(keys, list(metadata.get("tags") or []))

    # Name

    def _save_name(self, keys: PreviewKeys, suffix: str) -> None:
        """Rename the image from Enter or the checkmark, keeping its extension."""
        stem = st.session_state.get(keys.name_draft, "").strip()
        new_name = f"{stem}{suffix}"
        if new_name == keys.name or stem == Path(self.files.display_name(keys.name)).stem:
            st.session_state[keys.name_editing] = False
            st.session_state[keys.name_error] = None
            return
        try:
            self.actions.rename(keys.name, new_name)
        except httpx.HTTPError as error:
            st.session_state[keys.name_error] = LunaVisiaAPI.error_message(
                error, "Could not rename the image."
            )
            return
        st.session_state[keys.name_editing] = False
        st.session_state[keys.name_error] = None
        st.session_state[keys.renamed] = True
        st.toast(f"Renamed to {new_name}.", icon=":material/check:")

    def _render_name(self, keys: PreviewKeys, image_path: Path) -> None:
        display_name = self.files.display_name(image_path.name)
        box = st.container()
        self._marker(box, "lv-preview-details")
        icon_column, name_column, edit_column = box.columns(
            [0.6, 18.4, 1], vertical_alignment="center"
        )
        self._marker(icon_column, "lv-preview-static-icon")
        if st.session_state.get(keys.name_editing, False):
            name_column.text_input(
                "Name",
                key=keys.name_draft,
                max_chars=200,
                label_visibility="collapsed",
                placeholder=f"Letters, numbers, and underscores ({image_path.suffix} is kept)",
                on_change=self._save_name,
                args=(keys, image_path.suffix),
            )
            edit_column.button(
                ":material/check:",
                key=f"save_{keys.name_editing}",
                width="stretch",
                on_click=self._save_name,
                args=(keys, image_path.suffix),
            )
            if st.session_state.get(keys.name_error):
                box.error(st.session_state[keys.name_error])
            return
        name_column.markdown(
            f'<div class="lv-preview-caption">{html.escape(display_name)}</div>',
            unsafe_allow_html=True,
        )
        if edit_column.button(":material/edit:", key=f"open_{keys.name_editing}", width="stretch"):
            st.session_state[keys.name_editing] = True
            st.session_state[keys.name_draft] = Path(display_name).stem
            st.rerun(scope="fragment")

    # Caption

    def _save_caption(self, keys: PreviewKeys) -> None:
        """Save the inline caption field from Enter or the checkmark."""
        try:
            self._save(keys.name, caption=st.session_state.get(keys.caption_draft, ""))
            st.session_state[keys.caption_editing] = False
            st.session_state[keys.caption_error] = None
        except httpx.HTTPError as error:
            st.session_state[keys.caption_error] = f"Could not update description: {error}"

    def _render_caption(self, keys: PreviewKeys, caption: str) -> None:
        box = st.container()
        self._marker(box, "lv-preview-details")
        icon_column, caption_column, edit_column = box.columns(
            [0.6, 18.4, 1], vertical_alignment="center"
        )
        self._marker(icon_column, "lv-preview-static-icon")
        if st.session_state.get(keys.caption_editing, False):
            caption_column.text_input(
                "Description",
                key=keys.caption_draft,
                max_chars=500,
                label_visibility="collapsed",
                on_change=self._save_caption,
                args=(keys,),
            )
            edit_column.button(
                ":material/check:",
                key=f"save_{keys.caption_editing}",
                width="stretch",
                on_click=self._save_caption,
                args=(keys,),
            )
            if st.session_state.get(keys.caption_error):
                box.error(st.session_state[keys.caption_error])
            return
        caption_column.markdown(
            f'<div class="lv-preview-caption">{html.escape(caption)}</div>',
            unsafe_allow_html=True,
        )
        if edit_column.button(
            ":material/edit:", key=f"open_{keys.caption_editing}", width="stretch"
        ):
            st.session_state[keys.caption_editing] = True
            st.session_state[keys.caption_draft] = caption
            st.rerun(scope="fragment")

    # Tags

    @staticmethod
    def _normalized(tag: str) -> str:
        return " ".join(tag.casefold().split())

    def _save_tag(self, keys: PreviewKeys, tags: list[str]) -> None:
        """Save the inline tag field from Enter or the checkmark."""
        st.session_state[keys.tag_error] = None
        new_tag = st.session_state.get(keys.tag_draft, "").strip()
        if not new_tag:
            st.toast("Enter a tag name before adding it.", icon=":material/info:")
            return
        if self._normalized(new_tag) in {self._normalized(tag) for tag in tags}:
            st.toast("That tag is already on this image.", icon=":material/info:")
            return
        if len(tags) >= UIConfig.MAX_TAGS:
            st.toast(
                f"Maximum of {UIConfig.MAX_TAGS} tags reached. "
                "Remove a tag before adding another.",
                icon=":material/info:",
            )
            return
        try:
            self._save(keys.name, add_tags=[new_tag])
            st.session_state[keys.tag_adding] = False
            st.session_state[keys.pills_generation] += 1
        except httpx.HTTPError:
            st.toast("The tag could not be saved. Please try again.", icon=":material/info:")

    def _remove_deselected_tags(self, keys: PreviewKeys, tags: list[str]) -> None:
        """Remove tags deselected from the interactive tag pills."""
        selected = st.session_state.get(keys.pills) or []
        removed = [tag for tag in tags if tag not in selected]
        if not removed:
            return
        try:
            self._save(keys.name, remove_tags=removed)
            st.session_state[keys.tag_error] = None
        except httpx.HTTPError as error:
            st.session_state[keys.tag_error] = LunaVisiaAPI.error_message(
                error, "Could not remove the tag."
            )
        finally:
            st.session_state[keys.pills_generation] += 1

    def _render_tag_pills(self, column, keys: PreviewKeys, tags: list[str]) -> None:
        if not tags:
            return
        self._marker(column, "lv-preview-removable-tags")
        column.pills(
            "Tags",
            options=tags,
            default=tags,
            selection_mode="multi",
            key=keys.pills,
            on_change=self._remove_deselected_tags,
            args=(keys, tags),
            label_visibility="collapsed",
            width="stretch",
        )

    def _render_tags(self, keys: PreviewKeys, tags: list[str]) -> None:
        box = st.container()
        self._marker(box, "lv-preview-details")
        icon_column, _ = box.columns([0.6, 20.4], vertical_alignment="center")
        self._marker(icon_column, "lv-preview-static-icon")
        icon_column.button(
            ":material/style:", disabled=True, key=f"tags_icon_{keys.name}", width="stretch"
        )

        if st.session_state.get(keys.tag_adding, False):
            tags_column, input_column, action_column = box.columns(
                [12, 7, 1], vertical_alignment="center"
            )
            self._render_tag_pills(tags_column, keys, tags)
            input_column.text_input(
                "New tag",
                key=keys.tag_draft,
                placeholder="Tag name",
                label_visibility="collapsed",
                on_change=self._save_tag,
                args=(keys, tags),
            )
            self._marker(action_column, "lv-preview-icon-action")
            action_column.button(
                ":material/check:",
                key=f"save_{keys.tag_adding}",
                width="stretch",
                on_click=self._save_tag,
                args=(keys, tags),
            )
            if st.session_state.get(keys.tag_error):
                box.error(st.session_state[keys.tag_error])
            return

        tags_column, action_column = box.columns([20, 1], vertical_alignment="center")
        self._render_tag_pills(tags_column, keys, tags)
        self._marker(action_column, "lv-preview-icon-action")
        if action_column.button(":material/add:", key=f"open_{keys.tag_adding}", width="stretch"):
            st.session_state[keys.tag_adding] = True
            st.session_state[keys.tag_draft] = ""
            st.rerun(scope="fragment")
