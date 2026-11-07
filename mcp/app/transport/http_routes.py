"""REST routes used by the Luna Visia Streamlit interface and public image links."""

from collections.abc import Awaitable, Callable
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response

from app.core import tenancy
from app.integrations import model_usage
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

    @classmethod
    async def metadata_changes(cls, request: Request) -> list[tuple[str, MetadataChange]]:
        """Parse ``{"changes": [{"name", "caption", "add_tags", "remove_tags"}, ...]}``."""
        changes = (await cls.payload(request)).get("changes")
        if not isinstance(changes, list) or not all(
            isinstance(change, dict) and isinstance(change.get("name"), str) for change in changes
        ):
            raise ValueError("changes must be a list of objects that each have a name.")
        return [
            (
                change["name"],
                MetadataChange(change.get("caption"), change.get("add_tags"), change.get("remove_tags")),
            )
            for change in changes
        ]

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
        self.route("GET", "/api/models", self.models)
        self._register_icons()
        self._register_images()
        self._register_favorites()
        self._register_downloads()
        self._register_uploads()
        self._register_companion_images()
        self._register_hidden_images()

    async def health(self, request: Request) -> dict:
        return {"status": "ok", "service": "luna-visia"}

    async def models(self, request: Request) -> dict:
        """For Luna Sentia's model monitor: the models this app runs itself, outside Ollama.

        They run on the home's GPU, so only the owner sees them.
        """
        tenancy.require_owner("Seeing the app's own models")
        return model_usage.report("visia")

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

        async def embedded_metadata(request: Request):
            name = self.name(request)
            described = await anyio.to_thread.run_sync(
                lambda: visible.metadata_page(1, 0, 1, names=[name])
            )
            if not described["images"]:
                return JSONResponse({"error": f"Visible image {name!r} was not found."}, status_code=404)
            return described["images"][0]["embedded"]

        async def public_image(request: Request):
            identifier = self.name(request)
            # An unknown identifier may be a filename from an old link: only the owner's work.
            with tenancy.acting_as(visible.images.user_of_image_id(identifier) or tenancy.OWNER):
                path = visible.public_path(identifier)
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

        async def delete_many(request: Request):
            names = (await self.payload(request)).get("names", [])
            return await anyio.to_thread.run_sync(visible.remove_many, names)

        async def update_metadata_many(request: Request):
            changes = await self.metadata_changes(request)
            return await anyio.to_thread.run_sync(visible.update_metadata_many, changes)

        self.route("GET", "/api/metadata", metadata)
        self.route("GET", "/api/public/images/{name}", public_image)
        self.route("POST", "/api/images", upload, 201)
        self.route("POST", "/api/images/delete", delete_many)
        self.route("POST", "/api/images/metadata", update_metadata_many)
        self.route("DELETE", "/api/images/{name}", delete)
        self.route("POST", "/api/images/{name}/rename", rename)
        self.route("GET", "/api/images/{name}/metadata", embedded_metadata)
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
        """Let Luna Vita, Vibia, and Lumia store an image, or tag the images they use.

        The body names one source as upload_image takes it (image_url, content_base64,
        or a chat file reference) plus an optional name and tags. Identical bytes
        return the stored image as "duplicate", so a retried request is harmless.
        The tags route takes {"image_ids": [...], "tags": [...]} and adds the tags
        to each image, so every image another app uses carries that app's name.
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

        def companion(handler: Callable[[dict[str, Any]], Any]) -> Handler:
            async def endpoint(request: Request):
                if self.companion is None:
                    return JSONResponse(
                        {"error": "Companion uploads are off; set LUNAVISIA_COMPANION_TOKEN in Luna Visia."},
                        status_code=503,
                    )
                rejection = self.companion.rejection(request)
                if rejection is not None:
                    return rejection
                return await anyio.to_thread.run_sync(handler, await self.payload(request))

            return endpoint

        def tag(payload: dict[str, Any]) -> dict[str, Any]:
            return visible.tag_for_app(payload.get("image_ids"), payload.get("tags"))

        self.route("POST", "/api/companion/images", companion(store), 201)
        # Every app tags the Visia images it uses with its name, so Visia shows who uses what.
        self.route("POST", "/api/companion/images/tags", companion(tag))

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

        async def update_metadata_many(request: Request):
            changes = await self.metadata_changes(request)
            return await anyio.to_thread.run_sync(hidden.update_metadata_many, changes)

        # Off the event loop: a long selection means many file and database touches.
        async def hide(request: Request):
            names = (await self.payload(request)).get("names", [])
            return await anyio.to_thread.run_sync(hidden.set_hidden, names, True)

        async def restore(request: Request):
            names = (await self.payload(request)).get("names", [])
            return await anyio.to_thread.run_sync(hidden.set_hidden, names, False)

        async def delete(request: Request):
            names = (await self.payload(request)).get("names", [])
            return await anyio.to_thread.run_sync(hidden.remove_many, names)

        self.route("GET", "/api/hidden/images", images, protected=True)
        self.route("POST", "/api/hidden/images/metadata", update_metadata_many, protected=True)
        self.route(
            "POST", "/api/hidden/images/{name}/metadata", update_metadata, protected=True
        )
        self.route("POST", "/api/hidden/hide", hide, protected=True)
        self.route("POST", "/api/hidden/delete", delete, protected=True)
        self.route("POST", "/api/hidden/restore", restore, protected=True)
