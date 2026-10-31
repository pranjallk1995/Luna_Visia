"""REST routes used by the Luna Visia Streamlit interface and public image links."""

from collections.abc import Awaitable, Callable
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response

from app.transport.branding import ServerBranding
from app.transport.auth import APIAuthenticator
from app.core.models import MetadataChange
from app.integrations.remote import ImageSourceResolver
from app.services.application import VisiaService
from app.services.uploads import UploadLinkNotFound

Handler = Callable[[Request], Awaitable[Any]]
class VisiaHTTPRoutes:
    """Translate HTTP requests into service calls and results into JSON."""

    PUBLIC_IMAGE_HEADERS = {
        "Cache-Control": "private, max-age=300",
        "X-Content-Type-Options": "nosniff",
    }
    ICON_HEADERS = {
        "Cache-Control": "public, max-age=86400",
        "X-Content-Type-Options": "nosniff",
    }

    def __init__(
        self,
        server: FastMCP,
        service: VisiaService,
        branding: ServerBranding,
        api_token: str,
        sources: ImageSourceResolver | None = None,
        companion_token: str = "",
    ) -> None:
        self.server = server
        self.service = service
        self.branding = branding
        self.authenticator = APIAuthenticator(api_token)
        self.sources = sources
        self.companion = APIAuthenticator(companion_token) if companion_token else None
        self._register()

    @staticmethod
    def error(error: Exception) -> JSONResponse:
        status = 401 if isinstance(error, PermissionError) else 400
        return JSONResponse({"error": str(error)}, status_code=status)

    @staticmethod
    async def payload(request: Request) -> dict[str, Any]:
        """Parse a JSON object request body."""
        value = await request.json()
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object.")
        return value

    @classmethod
    async def metadata_change(cls, request: Request) -> MetadataChange:
        payload = await cls.payload(request)
        return MetadataChange(
            caption=payload.get("caption"),
            add_tags=payload.get("add_tags"),
            remove_tags=payload.get("remove_tags"),
        )

    @staticmethod
    def name(request: Request) -> str:
        return request.path_params["name"]

    def route(
        self,
        method: str,
        path: str,
        handler: Handler,
        status: int = 200,
        *,
        protected: bool = False,
    ) -> None:
        """Register ``handler`` with uniform error translation."""

        async def endpoint(request: Request) -> Response:
            try:
                if protected:
                    rejection = self.authenticator.rejection(request)
                    if rejection is not None:
                        return rejection
                result = await handler(request)
                if isinstance(result, Response):
                    return result
                return JSONResponse(result, status_code=status)
            except Exception as error:
                return self.error(error)

        self.server.custom_route(path, methods=[method])(endpoint)

    def _register(self) -> None:
        self.route("GET", "/health", self.health)
        self._register_icons()
        self._register_images()
        self._register_favorites()
        self._register_downloads()
        self._register_uploads()
        self._register_companion_images()
        self._register_hidden_images()

    async def health(self, request: Request) -> dict:
        return {"status": "ok", "service": "luna-visia"}

    def _register_icons(self) -> None:
        branding = self.branding

        async def favicon(request: Request):
            return FileResponse(
                branding.favicon_path(), media_type="image/x-icon", headers=self.ICON_HEADERS
            )

        async def icon(request: Request):
            return FileResponse(
                branding.icon_path(self.name(request)),
                media_type="image/png",
                headers=self.ICON_HEADERS,
            )

        async def home(request: Request):
            return HTMLResponse(branding.home_page(), headers=self.ICON_HEADERS)

        self.route("GET", "/", home)
        self.route("GET", "/favicon.ico", favicon)
        self.route("GET", "/icons/{name}", icon)

    def _register_images(self) -> None:
        visible = self.service.visible

        async def metadata(request: Request):
            return {"images": visible.list()}

        async def public_image(request: Request):
            path = visible.public_path(self.name(request))
            return FileResponse(
                path,
                headers={
                    "Content-Disposition": f'inline; filename="{path.name}"',
                    **self.PUBLIC_IMAGE_HEADERS,
                },
            )

        async def upload(request: Request):
            payload = await self.payload(request)
            created = await anyio.to_thread.run_sync(
                visible.ingest, payload["name"], payload["content_base64"]
            )
            return {"status": "created", **created}

        async def delete(request: Request):
            return visible.remove(self.name(request))

        async def rename(request: Request):
            payload = await self.payload(request)
            return visible.rename(self.name(request), payload.get("new_name", ""))

        async def update_metadata(request: Request):
            change = await self.metadata_change(request)
            return {
                "status": "metadata_updated",
                **visible.update_metadata(self.name(request), change),
            }

        self.route("GET", "/api/metadata", metadata)
        self.route("GET", "/api/public/images/{name}", public_image)
        self.route("POST", "/api/images", upload, 201)
        self.route("DELETE", "/api/images/{name}", delete)
        self.route("POST", "/api/images/{name}/rename", rename)
        self.route("POST", "/api/images/{name}/metadata", update_metadata)

    def _register_downloads(self) -> None:
        downloads = self.service.downloads

        async def download(request: Request):
            try:
                ticket, path = downloads.archive(request.path_params["token"])
            except FileNotFoundError as error:
                return JSONResponse({"error": str(error)}, status_code=404)
            return FileResponse(
                path,
                media_type="application/zip",
                filename=ticket.file_name,
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )

        self.route("GET", "/api/downloads/{token}", download)

    @staticmethod
    async def read_upload(request: Request, max_bytes: int) -> tuple[bytes, str | None]:
        """Read a raw or multipart (field ``file``) upload body, enforcing ``max_bytes``."""
        too_large = ValueError(f"Upload exceeds the {max_bytes // (1024 * 1024)} MiB limit.")
        if request.headers.get("content-type", "").startswith("multipart/form-data"):
            async with request.form(max_files=1) as form:
                upload = form.get("file")
                if upload is None or isinstance(upload, str):
                    raise ValueError("Send the image in a multipart field named 'file'.")
                data = await upload.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise too_large
                return data, upload.filename
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > max_bytes:
                raise too_large
        return bytes(data), None

    def _register_uploads(self) -> None:
        uploads = self.service.uploads

        async def receive(request: Request):
            data, file_name = await self.read_upload(request, uploads.max_bytes)
            try:
                return await anyio.to_thread.run_sync(
                    uploads.receive, request.path_params["token"], data, file_name
                )
            except UploadLinkNotFound as error:
                return JSONResponse({"error": str(error)}, status_code=404)

        self.route("POST", "/api/uploads/{token}", receive, 201)

    COMPANION_FIELDS = ("status", "message", "name", "image_id", "public_url", "caption", "tags")

    def _register_companion_images(self) -> None:
        """Let Luna Vita and Luna Vibia store an image and get its stable public URL.

        The body names one source as upload_image takes it (image_url, content_base64,
        or a chat file reference) plus an optional name and tags. Identical bytes
        return the stored image as "duplicate", so a retried request is harmless.
        """
        visible = self.service.visible

        def store(payload: dict[str, Any]) -> dict[str, Any]:
            if self.sources is None:
                raise RuntimeError("Image sources are not configured.")
            tags = payload.get("tags") or []
            if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
                raise ValueError("tags must be a list of strings.")
            source = self.sources.resolve(
                payload.get("content_base64"), payload.get("image_url"), payload.get("file")
            )
            name = payload.get("name")
            if not isinstance(name, str) or not name.strip():
                name = source.suggested_name
            stored = visible.upload(name, source.data)
            if tags:
                stored = {**stored, **visible.update_metadata(stored["name"], MetadataChange(add_tags=tags))}
            return {key: stored[key] for key in self.COMPANION_FIELDS if key in stored}

        async def companion_image(request: Request):
            if self.companion is None:
                return JSONResponse(
                    {"error": "Companion uploads are off; set LUNAVISIA_COMPANION_TOKEN in Luna Visia."},
                    status_code=503,
                )
            rejection = self.companion.rejection(request)
            if rejection is not None:
                return rejection
            return await anyio.to_thread.run_sync(store, await self.payload(request))

        self.route("POST", "/api/companion/images", companion_image, 201)

    def _register_favorites(self) -> None:
        visible = self.service.visible

        async def favorites(request: Request):
            return {"images": visible.favorites()}

        async def update_favorites(request: Request):
            payload = await self.payload(request)
            favorite = payload.get("favorite")
            if not isinstance(favorite, bool):
                raise ValueError("favorite must be true or false.")
            return visible.set_favorite(payload.get("names", []), favorite)

        self.route("GET", "/api/favorites", favorites)
        self.route("POST", "/api/favorites", update_favorites)

    def _register_hidden_images(self) -> None:
        hidden = self.service.hidden

        async def images(request: Request):
            return {"images": hidden.list()}

        async def update_metadata(request: Request):
            change = await self.metadata_change(request)
            return {
                "status": "metadata_updated",
                **hidden.update_metadata(self.name(request), change),
            }

        async def hide(request: Request):
            return hidden.set_hidden((await self.payload(request)).get("names", []), True)

        async def restore(request: Request):
            return hidden.set_hidden((await self.payload(request)).get("names", []), False)

        async def delete(request: Request):
            return hidden.remove_many((await self.payload(request)).get("names", []))

        self.route("GET", "/api/hidden/images", images, protected=True)
        self.route(
            "POST", "/api/hidden/images/{name}/metadata", update_metadata, protected=True
        )
        self.route("POST", "/api/hidden/hide", hide, protected=True)
        self.route("POST", "/api/hidden/delete", delete, protected=True)
        self.route("POST", "/api/hidden/restore", restore, protected=True)
