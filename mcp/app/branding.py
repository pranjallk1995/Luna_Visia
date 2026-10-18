"""The Luna Visia icon set for MCP server info and HTTP favicons."""

import base64
from pathlib import Path

from mcp.types import Icon

ASSET_DIR = Path(__file__).resolve().parent / "assets"


class ServerBranding:
    """Expose the bundled icons as MCP ``Icon`` entries and servable files.

    Server-info icons are embedded as ``data:`` URIs so connector clients can
    show them without reaching the server's public origin. The larger PNGs stay
    available over HTTP for clients that prefer a URL.
    """

    FAVICON = "favicon.ico"
    ICON_SIZES = (48, 96, 192, 512)
    SERVER_INFO_ICON_SIZES = (48, 96)

    def __init__(self, asset_dir: Path = ASSET_DIR) -> None:
        self.asset_dir = asset_dir

    @staticmethod
    def icon_name(size: int) -> str:
        return f"icon-{size}.png"

    def favicon_path(self) -> Path:
        return self.asset_dir / self.FAVICON

    def icon_path(self, name: str) -> Path:
        """Resolve one bundled PNG by name, rejecting anything else."""
        if name not in {self.icon_name(size) for size in self.ICON_SIZES}:
            raise FileNotFoundError(f"Icon {name!r} was not found.")
        return self.asset_dir / name

    def mcp_icons(self) -> list[Icon]:
        """Return the icons advertised in the MCP ``initialize`` server info."""
        icons = []
        for size in self.SERVER_INFO_ICON_SIZES:
            data = base64.b64encode(self.icon_path(self.icon_name(size)).read_bytes())
            icons.append(
                Icon(
                    src=f"data:image/png;base64,{data.decode('ascii')}",
                    mimeType="image/png",
                    sizes=[f"{size}x{size}"],
                )
            )
        return icons
