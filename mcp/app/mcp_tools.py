"""MCP tools that let LLM clients manage Luna Visia images."""

from collections.abc import Callable
from typing import Annotated, Any, Literal, TypeVar

import anyio
from mcp.server.fastmcp import FastMCP, Image as MCPImage
from pydantic import BaseModel, ConfigDict, WithJsonSchema

from .models import MetadataChange
from .remote import ImageSourceResolver
from .services import VisiaService

Result = TypeVar("Result")

# ChatGPT passes attachments to parameters listed here as {download_url, file_id}.
FILE_PARAMS_META = {"openai/fileParams": ["file"]}


class FileReference(BaseModel):
    """A client-hosted attachment, as passed by ChatGPT for file parameters."""

    model_config = ConfigDict(extra="allow")

    download_url: str
    file_id: str | None = None


# Inline (no $ref) so every LLM client can read the parameter shape.
FileInput = Annotated[
    FileReference | None,
    WithJsonSchema(
        {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "download_url": {"type": "string"},
                        "file_id": {"type": "string"},
                    },
                    "required": ["download_url"],
                },
                {"type": "null"},
            ],
            "description": "An attached image file passed by the chat client.",
        }
    ),
]


async def offload(function: Callable[..., Result], *args: Any) -> Result:
    """Run slow, blocking work (vision analysis, archives) off the event loop.

    FastMCP calls synchronous tools on the event loop, so a blocking tool would
    stall every other request, including the web UI and health checks.
    """
    return await anyio.to_thread.run_sync(function, *args)


class VisiaMCPTools:
    """Register Luna Visia's MCP tool surface on a FastMCP server.

    Hidden images are never reachable through these public tools.
    """

    def __init__(
        self,
        server: FastMCP,
        service: VisiaService,
        sources: ImageSourceResolver,
    ) -> None:
        self.server = server
        self.service = service
        self.sources = sources
        self._register_library_tools()
        self._register_delivery_tools()
        self._register_mutation_tools()

    def preview(self, names: list[str]) -> list:
        """Render a preview as image content plus a JSON key to its tiles."""
        result = self.service.previews.preview(names)
        return [MCPImage(data=result.jpeg, format="jpeg"), result.summary()]

    async def with_preview(self, listing: dict[str, Any], items_key: str, include_preview: bool):
        """Return ``listing``, adding a contact sheet of its first images on request."""
        names = [item["name"] for item in listing[items_key]][: self.service.previews.max_images]
        if not include_preview or not names:
            return listing
        image, summary = await offload(self.preview, names)
        return [listing, image, {"preview": summary}]

    def _register_library_tools(self) -> None:
        server, visible, search, indexer = (
            self.server,
            self.service.visible,
            self.service.search,
            self.service.indexer,
        )

        @server.tool()
        async def list_images(limit: int = 50, offset: int = 0, include_preview: bool = False):
            """List visible images newest first, one page at a time.

            Each image has its exact name, caption, tags, favorite flag, size, and
            public_url. Follow next_offset for the next page (it is null on the
            last page). Set include_preview=true to also get a numbered contact
            sheet of the first 20 images on the page.
            """
            listing = visible.list_page(limit, offset, self.service.search.limit_max)
            return await self.with_preview(listing, "images", include_preview)

        @server.tool()
        async def search_images(
            query: str = "",
            tags: list[str] | None = None,
            match_all_tags: bool = False,
            limit: int = 50,
            offset: int = 0,
            include_preview: bool = False,
        ):
            """Search visible filenames, captions, and tags without opening files.

            Results include each image's public_url; follow next_offset for more.
            Set include_preview=true to also get a numbered contact sheet of the
            first 20 results.
            """
            results = search.search(query, tags, match_all_tags, limit, offset)
            return await self.with_preview(results, "results", include_preview)

        @server.tool()
        async def backfill_metadata() -> dict[str, Any]:
            """Analyze files missing current metadata; idempotent by hash/model."""
            return await offload(indexer.backfill)

    def _register_delivery_tools(self) -> None:
        server, visible, urls, downloads = (
            self.server,
            self.service.visible,
            self.service.urls,
            self.service.downloads,
        )

        @server.tool()
        async def view_image(name: str):
            """Show a preview of one visible image, with its caption and public_url."""
            return await offload(self.preview, [name])

        @server.tool()
        def get_image_url(name: str) -> dict[str, str]:
            """Return the public HTTPS URL for one non-hidden stored image."""
            return {"name": name, "url": visible.public_url(name)}

        @server.tool()
        def set_public_base_url(public_base_url: str) -> dict[str, Any]:
            """Change the public HTTPS origin used by all Luna Visia image URLs.

            Supply the full externally reachable base, including any path prefix,
            for example https://device.tailnet.ts.net/vault. Every stored image URL
            is regenerated immediately; no service restart is required.
            """
            return urls.update(public_base_url)

        @server.tool()
        async def fetch_images(names: list[str], delivery: Literal["display", "zip"] = "display"):
            """Show visible images in chat, or package them as a downloadable ZIP.

            Use exact filenames returned by list_images or search_images.
            - display: one preview, or a numbered contact sheet for up to 20
              images, plus each image's caption and public_url. Names that are no
              longer available are listed under "unavailable" instead of failing.
            - zip: packages the original files on the server and returns a
              download_url (at most 200 images, 2 GiB). Give the user that link
              exactly as returned; it expires at expires_at (24 hours) and the
              ZIP is not embedded in this result.
            Hidden images are never available through this public tool.
            """
            if delivery == "zip":
                return await offload(downloads.create_zip, names)
            return await offload(self.preview, names)

    def _register_mutation_tools(self) -> None:
        server, visible, sources, uploads = (
            self.server,
            self.service.visible,
            self.sources,
            self.service.uploads,
        )

        def upload(name: str | None, base64_data=None, image_url=None, file=None) -> dict[str, Any]:
            source = sources.resolve(base64_data, image_url, file.model_dump() if file else None)
            return visible.upload(name or source.suggested_name, source.data)

        @server.tool(meta=FILE_PARAMS_META)
        async def upload_image(
            name: str | None = None,
            file: FileInput = None,
            image_url: str | None = None,
            content_base64: str | None = None,
        ) -> dict[str, Any]:
            """Add an image to Luna Visia; it is analyzed for a caption and tags.

            Provide exactly one source:
            - file: an image the user attached in chat (ChatGPT supplies it).
            - image_url: a public HTTPS image URL or an image data URL.
            - content_base64: Base64 image bytes; only practical for small images.
            To upload a local file from a shell or code sandbox, use
            create_upload_link instead of Base64.

            name is optional. The extension is corrected to match the image
            format, a taken name gets a numeric suffix, and an exact duplicate of a
            stored image returns status "duplicate" instead of a second copy.
            Maximum image size: 50 MiB.
            """
            return await offload(upload, name, content_base64, image_url, file)

        @server.tool()
        async def add_image(name: str, content_base64: str) -> dict[str, Any]:
            """Add a small image from Base64. Prefer upload_image for attachments and URLs."""
            return await offload(upload, name, content_base64)

        @server.tool()
        def create_upload_link(name: str, replace: bool = False) -> dict[str, Any]:
            """Create a single-use HTTPS link for uploading a local image file.

            Use this when you can run commands (a shell or code sandbox) and have
            the image as a file: POST its bytes to upload_url, for example with the
            returned curl_example. The link expires after 15 minutes. With
            replace=true, name must be an existing visible image to overwrite.
            """
            return uploads.create_link(name, replace)

        @server.tool(meta=FILE_PARAMS_META)
        async def modify_image(
            name: str,
            content_base64: str | None = None,
            image_url: str | None = None,
            file: FileInput = None,
        ) -> dict[str, Any]:
            """Analyze and atomically replace a non-hidden image and its metadata.

            Provide exactly one of file, image_url, or content_base64; the new image
            must have the same format as the existing file's extension.
            """

            def replace() -> dict[str, Any]:
                source = sources.resolve(content_base64, image_url, file.model_dump() if file else None)
                return {"status": "modified", **visible.ingest_bytes(name, source.data, replace=True)}

            return await offload(replace)

        @server.tool()
        def update_image_metadata(
            name: str,
            caption: str | None = None,
            add_tags: list[str] | None = None,
            remove_tags: list[str] | None = None,
        ) -> dict[str, Any]:
            """Edit a visible image caption and add or remove searchable tags."""
            change = MetadataChange(caption, add_tags, remove_tags)
            return {"status": "metadata_updated", **visible.update_metadata(name, change)}

        @server.tool()
        def rename_image(name: str, new_name: str) -> dict[str, Any]:
            """Rename an image using only letters, numbers, and underscores.

            Keep the existing image extension. Spaces, hyphens, and other
            special characters are not allowed. The immutable ID and public URL
            remain unchanged.
            """
            return visible.rename(name, new_name)

        @server.tool()
        def set_favorite_images(names: list[str], favorite: bool) -> dict[str, Any]:
            """Add or remove visible images from Favorites.

            Use exact filenames returned by list_images or search_images. Set
            favorite to true to add the images or false to remove them.
            """
            return visible.set_favorite(names, favorite)

        @server.tool()
        def remove_image(name: str) -> dict[str, str]:
            """Remove a non-hidden image and its persisted metadata consistently."""
            return visible.remove(name)
