"""Upload dialog: send images for AI analysis and show the generated metadata."""

import base64
import time
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import httpx
import streamlit as st

from views.shared import ImageLabel, VisiaTab


class UploadDialog(VisiaTab):
    """Upload new files, retry failures, and keep the returned metadata."""

    PROGRESS_TICK_SECONDS = 0.5

    def open(self) -> None:
        @st.dialog("Upload images", width="large")
        def upload_dialog() -> None:
            self.render()

        upload_dialog()

    @staticmethod
    def stored_name(display_name: str) -> str:
        """Prefix uploads so identical filenames never collide in the vault."""
        return f"{uuid4().hex}_{display_name}"

    def _post_with_progress(
        self, display_name: str, content: bytes, progress, progress_value: float
    ) -> dict:
        """Keep progress feedback alive during the blocking vision request."""
        encoded = base64.b64encode(content).decode("ascii")
        started_at = time.monotonic()
        tick = 0
        with ThreadPoolExecutor(max_workers=1) as executor:
            request = executor.submit(
                self.api.upload_image, self.stored_name(display_name), encoded
            )
            while not request.done():
                elapsed = int(time.monotonic() - started_at)
                dots = "." * ((tick % 3) + 1)
                progress.progress(
                    progress_value,
                    text=(
                        f"Analyzing {display_name} with the vision model. "
                        f"Time: {elapsed}s elapsed{dots} "
                    ),
                )
                tick += 1
                time.sleep(self.PROGRESS_TICK_SECONDS)
            return request.result()

    @staticmethod
    def _record_success(upload_id: str, metadata: dict) -> None:
        st.session_state.saved_uploads.add(upload_id)
        st.session_state.uploaded_images.append({"upload_id": upload_id, **metadata})

    def upload_images(self, uploads: list) -> None:
        """Upload files not yet saved this session, tracking overall progress."""
        pending = []
        for upload in uploads:
            content = upload.getbuffer()
            upload_id = sha256(content).hexdigest()
            if upload_id not in st.session_state.saved_uploads:
                pending.append((upload, upload_id, content))
        if not pending:
            return

        progress = st.progress(0.0, text="Preparing images for upload...")
        total_bytes = sum(len(content) for _, _, content in pending)
        written_bytes = 0
        try:
            for upload, upload_id, content in pending:
                display_name = Path(upload.name).name
                fraction = written_bytes / total_bytes if total_bytes else 0.0
                progress.progress(
                    fraction,
                    text=(
                        f"Analyzing {display_name} with the local vision model. "
                        "The first request may include model initialization."
                    ),
                )
                try:
                    metadata = self._post_with_progress(display_name, content, progress, fraction)
                except httpx.HTTPError as error:
                    st.session_state.failed_uploads[upload_id] = {
                        "name": display_name,
                        "content": bytes(content),
                    }
                    st.error(f"Could not upload {display_name}: {error}")
                    written_bytes += len(content)
                    continue
                written_bytes += len(content)
                progress.progress(
                    written_bytes / total_bytes if total_bytes else 1.0,
                    text=f"Analysis and upload complete for {display_name}",
                )
                self._record_success(upload_id, metadata)
        finally:
            progress.empty()

    def retry_upload(self, upload_id: str) -> None:
        """Retry one previously failed upload."""
        failed = st.session_state.failed_uploads[upload_id]
        progress = st.progress(0.0, text="Preparing image for analysis...")
        try:
            metadata = self._post_with_progress(failed["name"], failed["content"], progress, 0.0)
            progress.progress(1.0, text=f"Analysis complete for {failed['name']}.")
            self._record_success(upload_id, metadata)
            del st.session_state.failed_uploads[upload_id]
        finally:
            progress.empty()

    @staticmethod
    def clear_results() -> None:
        """Reset upload results and recreate the uploader widget."""
        st.session_state.uploaded_images = []
        st.session_state.saved_uploads = set()
        st.session_state.uploader_key += 1

    def render(self) -> None:
        uploads = st.file_uploader(
            ":material/upload_file: Drag and drop image files",
            type=list(self.context.config.UPLOAD_TYPES),
            accept_multiple_files=True,
            key=f"image_uploader_{st.session_state.uploader_key}",
        )
        if uploads:
            self.upload_images(uploads)
        self._render_retries()
        self._render_results()

    def _render_retries(self) -> None:
        for upload_id, failed in st.session_state.failed_uploads.copy().items():
            if st.button(f"Retry {failed['name']}", key=f"retry_{upload_id}"):
                try:
                    self.retry_upload(upload_id)
                    st.rerun()
                except httpx.HTTPError as error:
                    st.error(f"Could not upload {failed['name']}: {error}")

    def _render_results(self) -> None:
        for image in st.session_state.uploaded_images:
            with st.container(border=True):
                ImageLabel.render(st, image["name"])
                st.write(image["caption"])
                st.caption("  ".join(f"#{tag}" for tag in image["tags"]))
        if st.session_state.uploaded_images and st.button(
            "Clear", icon=":material/refresh:", width="stretch"
        ):
            self.clear_results()
            st.rerun()
