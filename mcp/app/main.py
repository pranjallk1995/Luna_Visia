"""Compose and run the Luna Visia MCP and HTTP server."""

import uvicorn
from mcp.server.fastmcp import FastMCP

from .branding import ServerBranding
from .config import AppConfig
from .database import Database
from .http_routes import VisiaHTTPRoutes
from .mcp_tools import VisiaMCPTools
from .models import MetadataRules
from .remote import ImageSourceResolver
from .repositories import CredentialRepository, ImageRepository, PublicURLRepository
from .security import CredentialPolicy, HiddenVaultAuth, SessionStore
from .services import VisiaService
from .vision import OllamaVisionAnalyzer


class LunaVisiaApplication:
    """Compose and own the concrete Luna Visia dependencies."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.database = Database(config.database_url)
        self.database.initialize_schema()
        self.service = VisiaService.create(
            config,
            ImageRepository(self.database),
            PublicURLRepository(self.database),
            OllamaVisionAnalyzer(
                config.ollama_url,
                config.vision_model,
                MetadataRules(config.max_tags, config.search_stop_words),
            ),
        )
        self.auth = HiddenVaultAuth(
            CredentialRepository(self.database),
            CredentialPolicy(config.password_hash_iterations),
            SessionStore(config.hidden_session_seconds),
        )
        self.branding = ServerBranding()
        self.mcp = FastMCP(
            "Luna Visia", host=config.host, port=config.port, icons=self.branding.mcp_icons()
        )
        VisiaMCPTools(
            self.mcp,
            self.service,
            ImageSourceResolver(
                self.service.visible.decoder, config.max_image_bytes, config.max_upload_redirects
            ),
        )
        VisiaHTTPRoutes(self.mcp, self.service, self.auth, self.branding)

    def run(self) -> None:
        """Serve Luna Visia over the streamable HTTP transport."""
        uvicorn.run(self.mcp.streamable_http_app(), host=self.config.host, port=self.config.port)


application = LunaVisiaApplication(AppConfig.from_env())
mcp = application.mcp

if __name__ == "__main__":
    application.run()
