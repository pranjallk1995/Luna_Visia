"""MCP tools that let LLM clients manage Luna Visia images."""

from typing import Any, Literal

from mcp.server.fastmcp import FastMCP, Image as MCPImage

from .media import PreviewRenderer
from .models import MetadataChange
from .remote import ImageSourceResolver
from .services import VisiaService


class VisiaMCPTools:
    """Register Luna Visia's MCP tool surface on a FastMCP server.

    Hidden images are never reachable through these public tools.
    """

    def __init__(
        self,
        server: FastMCP,
        service: VisiaService,
        sources: ImageSourceResolver,
        previews: PreviewRenderer,
    ) -> None:
        self.server = server
        self.service = service
        self.sources = sources
        self.previews = previews
        self._register_library_tools()
        self._register_delivery_tools()
        self._register_mutation_tools()

    def preview(self, paths) -> MCPImage:
        return MCPImage(data=self.previews.render(paths), format="jpeg")

    def _register_library_tools(self) -> None:
        server, visible, search, indexer = (
            self.server,
            self.service.visible,
            self.service.search,
            self.service.indexer,
        )

        @server.tool()
        def list_images() -> list[dict[str, Any]]:
            """List non-hidden stored images with persisted captions and tags."""
            return visible.list()

        @server.tool()
        def search_images(
            query: str = "",
            tags: list[str] | None = None,
            match_all_tags: bool = False,
            limit: int = 50,
            offset: int = 0,
        ) -> dict[str, Any]:
            """Search non-hidden filenames, captions, and tags without opening files."""
            return search.search(query, tags, match_all_tags, limit, offset)

        @server.tool()
        def backfill_metadata() -> dict[str, Any]:
            """Analyze files missing current metadata; idempotent by hash/model."""
            return indexer.backfill()

    def _register_delivery_tools(self) -> None:
        server, visible, urls, downloads = (
            self.server,
            self.service.visible,
            self.service.urls,
            self.service.downloads,
        )

        @server.tool()
        def view_image(name: str) -> MCPImage:
            """Return a displayable preview of one non-hidden stored image."""
            return self.preview([visible.path(name)])

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
        def fetch_images(names: list[str], delivery: Literal["display", "zip"] = "display"):
            """Fetch visible images for chat display or as a downloadable ZIP.

            Use exact filenames returned by list_images or search_images.
            - display: returns one preview or a labeled contact sheet (at most 20
              images, 50 MiB of originals).
            - zip: packages the original files on the server and returns a
              download_url (at most 200 images, 2 GiB). Give the user that link
              exactly as returned; it expires at expires_at (30 minutes) and the
              ZIP is not embedded in this result.
            Hidden images are never available through this public tool.
            """
            if delivery == "zip":
                return downloads.create_zip(names)
            return [self.preview(visible.select_paths(names))]

    def _register_mutation_tools(self) -> None:
        server, visible, sources = self.server, self.service.visible, self.sources

        @server.tool()
        def add_image(name: str, content_base64: str) -> dict[str, Any]:
            """Analyze and add an image, persisting its caption and tags."""
            return {"status": "created", **visible.ingest(name, content_base64, replace=False)}

        @server.tool()
        def upload_image(
            name: str,
            content_base64: str | None = None,
            image_url: str | None = None,
        ) -> dict[str, Any]:
            """Upload an image from a ChatGPT attachment, Base64, or HTTPS URL.

            Provide exactly one of:
            - content_base64: Base64-encoded image data
            - image_url: Public HTTPS image URL or data URL

            ChatGPT attachment paths may be converted to temporary
            HTTPS URLs by the tool infrastructure.

            Maximum image size: 50 MiB.
            """
            content = sources.resolve(content_base64, image_url)
            return {"status": "created", **visible.ingest(name, content, replace=False)}

        @server.tool()
        def modify_image(name: str, content_base64: str) -> dict[str, Any]:
            """Analyze and atomically replace a non-hidden image and its metadata."""
            return {"status": "modified", **visible.ingest(name, content_base64, replace=True)}

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
