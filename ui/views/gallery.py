"""Paginated grid/list gallery shared by the View, Favorites, and Hidden tabs."""

import html
from pathlib import Path

import httpx
import streamlit as st
from st_img_selector import st_img_selector

from services.images import ImageCollection
from services.state import SessionState
from views.actions import GalleryActions
from views.pager import GalleryPager
from views.preview_dialog import ImagePreviewDialog
from views.shared import ImageLabel, VisiaContext


class ImageGallery:
    """Render image cards and delegate what each card can do to ``actions``.

    Widget keys keep the ``view_`` prefix in every tab; only one tab renders
    per run, so they never collide.
    """

    PAGE_KEY = "view_page"
    EMPTY_MESSAGE = "No images are currently stored in Luna Visia."

    def __init__(self, context: VisiaContext, actions: GalleryActions) -> None:
        self.config = context.config
        self.files = context.files
        self.layout = context.layout
        self.actions = actions
        self.pager = GalleryPager(context.config.images_per_page, self.PAGE_KEY)

    def render(self, collection: ImageCollection) -> None:
        if not collection.images:
            self.pager.reset()
            SessionState.clear_pending_actions()
            st.info(self.EMPTY_MESSAGE)
            return

        SessionState.clear_pending_actions({path.name for path in collection.images})
        favorite_names = self.actions.favorite_names()
        generation = st.session_state.view_generation
        page, page_images = self.pager.paginate(collection.images)
        self.pager.render_transition_marker()
        render_page = self._render_list if SessionState.is_list_view() else self._render_grid
        opened = render_page(page_images, collection.metadata_by_name, favorite_names, generation, page)
        self.pager.render_footer(collection.images, f"view_{generation}")
        self.pager.finish_transition()

        self._confirm_pending_delete()
        if self.actions.allows_hiding:
            self._confirm_pending_hide()
        if opened is not None:
            SessionState.bump_selector(opened.name)
            ImagePreviewDialog(self.files, self.actions, collection.metadata_by_name).open(opened)

    # Layouts

    def _render_grid(
        self,
        images: list[Path],
        metadata_by_name: dict[str, dict],
        favorite_names: set[str],
        generation: int,
        page: int,
    ) -> Path | None:
        opened = None
        columns_per_row = self.layout.gallery_columns()
        for row_start in range(0, len(images), columns_per_row):
            row_images = images[row_start : row_start + columns_per_row]
            for item_index, (column, image_path) in enumerate(
                zip(st.columns(columns_per_row), row_images)
            ):
                with column:
                    clicked = self._render_thumbnail(
                        image_path,
                        f"view_selector_{generation}_{page}_{row_start}_{item_index}_"
                        f"{SessionState.selector_generation(image_path.name)}",
                        favorite_names,
                        generation,
                        "view_grid_thumb",
                    )
                self._render_card_details(
                    column, image_path, metadata_by_name.get(image_path.name), generation, "view_grid"
                )
                if clicked and opened is None:
                    opened = image_path
        return opened

    def _render_list(
        self,
        images: list[Path],
        metadata_by_name: dict[str, dict],
        favorite_names: set[str],
        generation: int,
        page: int,
    ) -> Path | None:
        opened = None
        for image_path in images:
            with st.container(border=True):
                thumbnail_column, detail_column = st.columns([1, 3], vertical_alignment="center")
                with thumbnail_column:
                    clicked = self._render_thumbnail(
                        image_path,
                        f"view_list_selector_{generation}_{page}_{image_path.name}_"
                        f"{SessionState.selector_generation(image_path.name)}",
                        favorite_names,
                        generation,
                        "view_list_thumb",
                    )
                self._render_card_details(
                    detail_column,
                    image_path,
                    metadata_by_name.get(image_path.name),
                    generation,
                    "view_list",
                )
                if clicked:
                    opened = image_path
        return opened

    # Card parts

    def _render_thumbnail(
        self,
        image_path: Path,
        selector_key: str,
        favorite_names: set[str],
        generation: int,
        overlay_prefix: str,
    ) -> bool:
        """Render a clickable thumbnail and report whether it was clicked."""
        clicked = st_img_selector(
            images=[self.files.thumbnail(image_path)],
            value=[],
            corner_radius=self.config.gallery_corner_radius,
            selection_color=self.config.gallery_selection_color,
            img_per_row=1,
            border_thickness=self.config.gallery_border_thickness,
            max_row_height=self.config.gallery_max_row_height,
            key=selector_key,
        )
        if self.actions.shows_favorites:
            self._render_favorite_overlay(image_path, favorite_names, generation, overlay_prefix)
        return bool(clicked)

    @st.fragment
    def _render_favorite_overlay(
        self, image_path: Path, favorite_names: set[str], generation: int, key_prefix: str
    ) -> None:
        """Overlay the Favorite toggle at the thumbnail's top-right corner."""
        st.markdown('<span class="lv-thumbnail-shell"></span>', unsafe_allow_html=True)
        _, favorite_column = st.columns([6, 1])
        state_key = f"{key_prefix}_favorite_state_{image_path.name}"
        state_generation_key = f"{state_key}_generation"
        if st.session_state.get(state_generation_key) != generation:
            st.session_state[state_key] = image_path.name in favorite_names
            st.session_state[state_generation_key] = generation
        is_favorite = st.session_state[state_key]
        marker_class = "lv-favorite-overlay lv-favorite-active" if is_favorite else "lv-favorite-overlay"
        favorite_column.markdown(f'<span class="{marker_class}"></span>', unsafe_allow_html=True)
        if favorite_column.button(
            ":material/favorite_border:",
            type="primary" if is_favorite else "secondary",
            key=f"{key_prefix}_favorite_{generation}_{image_path.name}",
            width="stretch",
        ):
            try:
                self.actions.set_favorite(image_path.name, not is_favorite)
                st.session_state[state_key] = not is_favorite
                st.rerun(scope="fragment")
            except httpx.HTTPError as error:
                st.error(f"Could not update Favorite status: {error}")

    def _render_card_details(
        self, container, image_path: Path, metadata: dict | None, generation: int, key_prefix: str
    ) -> None:
        name_column, menu_column = container.columns([8, 1], vertical_alignment="center")
        ImageLabel.render(name_column, image_path.name)
        self._render_menu(menu_column, image_path, generation, key_prefix)
        self._render_facts(container, image_path, metadata)

    def _render_menu(self, container, image_path: Path, generation: int, key_prefix: str) -> None:
        """Render download, delete, and collection-specific actions in a popover."""
        extra_actions = self.actions.menu_actions()
        with container.popover(":material/more_vert:", use_container_width=True):
            download_column, delete_column, *extra_columns = st.columns(
                2 + len(extra_actions), gap="small"
            )
            download_column.download_button(
                ":material/download:",
                data=image_path.read_bytes(),
                file_name=self.files.display_name(image_path.name),
                mime=self.files.mime_type(image_path),
                width="stretch",
                key=f"{key_prefix}_download_{generation}_{image_path.name}",
            )
            if delete_column.button(
                ":material/delete:",
                width="stretch",
                key=f"{key_prefix}_delete_{generation}_{image_path.name}",
            ):
                SessionState.request_delete(image_path.name)
                st.rerun()
            for column, action in zip(extra_columns, extra_actions):
                if column.button(
                    action.icon,
                    width="stretch",
                    key=f"{key_prefix}_{action.key}_{generation}_{image_path.name}",
                ):
                    action.run(image_path.name)
                    st.rerun()

    def _render_facts(self, container, image_path: Path, metadata: dict | None) -> None:
        """Render one clipped tag row followed by size and resolution."""
        tags = (metadata or {}).get("tags", [])
        tag_html = "".join(
            f'<span class="lv-tag lv-tag-{index % 6}">{html.escape(str(tag))}</span>'
            for index, tag in enumerate(tags)
        )
        size, width, height = self.files.facts(image_path)
        container.markdown(
            f'<div class="lv-card-tags">{tag_html}</div>'
            f'<div class="lv-card-facts"><span>{self.files.size_label(size)}</span>'
            f"<span>·</span><span>{width} × {height}</span></div>",
            unsafe_allow_html=True,
        )

    # Confirmation dialogs

    def _confirm_pending_delete(self) -> None:
        name = st.session_state.pending_view_delete
        if not name:
            return

        @st.dialog("Delete image?", width="small")
        def confirm_view_deletion() -> None:
            st.write(f"Permanently delete **{self.files.display_name(name)}**? ")
            cancel_column, delete_column = st.columns(2)
            if cancel_column.button("Cancel", width="stretch", key="cancel_view_delete"):
                st.session_state.pending_view_delete = None
                st.rerun()
            if delete_column.button(
                "Delete",
                icon=":material/delete:",
                type="primary",
                width="stretch",
                key="confirm_view_delete",
            ):
                try:
                    self.actions.delete(name)
                    st.rerun()
                except httpx.HTTPError as error:
                    st.error(f"Could not delete the image: {error}")

        confirm_view_deletion()

    def _confirm_pending_hide(self) -> None:
        name = st.session_state.pending_view_hide
        if not name:
            return

        @st.dialog("Hide image?", width="small")
        def confirm_view_hide() -> None:
            st.write(f"Move **{self.files.display_name(name)}** to Hidden Images?")
            cancel_column, hide_column = st.columns(2)
            if cancel_column.button("Cancel", width="stretch", key="cancel_view_hide"):
                st.session_state.pending_view_hide = None
                st.rerun()
            if hide_column.button(
                "Hide",
                icon=":material/visibility_off:",
                type="primary",
                width="stretch",
                key="confirm_view_hide",
            ):
                try:
                    self.actions.hide(name)
                    st.rerun()
                except PermissionError as error:
                    st.error(str(error))
                except httpx.HTTPError as error:
                    st.error(f"Could not hide the image: {error}")

        confirm_view_hide()
