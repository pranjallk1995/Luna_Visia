"""The UI gateway: serves the React app, enforces sign-in, and fronts the backend.

The browser never sees the backend's API token. Every data route checks the
shared Luna session and this app's allowlist first, and only the routes the UI
uses are forwarded.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any
from urllib.parse import quote

import anyio
import httpx
from PIL import UnidentifiedImageError
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from config import SSOConfig, UIConfig
from hidden_pin import LOCKED, HiddenPin
from images import ImageFiles
from sso import CURRENT_USER, LunaSingleSignOn, send_user, signed_in_user

NAME = r"[^/]+"
# The backend routes the browser app may reach, as (method, path pattern).
PROXIED_ROUTES = tuple(
    (method, re.compile(pattern))
    for method, pattern in (
        ("POST", r"/api/images"),
        ("POST", r"/api/images/(delete|metadata)"),
        ("DELETE", rf"/api/images/{NAME}"),
        ("POST", rf"/api/images/{NAME}/rename"),
        ("GET", rf"/api/images/{NAME}/metadata"),
        ("POST", rf"/api/images/{NAME}/metadata"),
        ("POST", r"/api/favorites"),
        ("POST", r"/api/hidden/(hide|restore|delete)"),
        ("POST", rf"/api/hidden/images/{NAME}/metadata"),
        ("POST", r"/api/hidden/images/metadata"),
    )
)
# Routes that touch a whole batch of images, so they get longer than one image's change.
BATCH_ROUTES = re.compile(r"/api/(favorites|images/(delete|metadata)|hidden/(hide|restore|delete|images/metadata))")
# Changes to hidden items, which wait for the PIN. Hiding a visible item does not.
HIDDEN_CHANGES = re.compile(r"/api/hidden/(?!hide\Z).+")
SENTIA_AWAY = "Luna Sentia, which checks your PIN, is unavailable. Please try again."
UNAVAILABLE = "Luna Visia's backend is unavailable. Please try again."


class StaticApp(StaticFiles):
    """Serve the built app: hashed bundles are cached forever, the page itself never."""

    async def get_response(self, path: str, scope: Any) -> Response:
        response = await super().get_response(path, scope)
        immutable = path.startswith("assets/")
        response.headers["Cache-Control"] = UIConfig.IMMUTABLE_CACHE_CONTROL if immutable else "no-cache"
        return response


class UIGateway:
    """Translate browser requests into session checks, vault reads, and backend calls."""

    def __init__(
        self,
        config: UIConfig,
        backend_transport: httpx.AsyncBaseTransport | None = None,
        sso_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self.vaults: dict[str, ImageFiles] = {}
        self.sso = LunaSingleSignOn(config.allowed_emails, config.sso, sso_transport, app="visia")
        self.pin = HiddenPin(config.sso, sso_transport)
        self.backend = httpx.AsyncClient(
            event_hooks={"request": [send_user]},
            base_url=config.api_url,
            headers={"Authorization": f"Bearer {config.api_token}"},
            timeout=UIConfig.API_TIMEOUT_SECONDS,
            transport=backend_transport,
        )

    @property
    def files(self) -> ImageFiles:
        """The signed-in person's own vault folder, with caches of its own."""
        user_id = signed_in_user()
        if user_id not in self.vaults:
            self.vaults[user_id] = ImageFiles(self.config.image_dir / f"u_{user_id}")
        return self.vaults[user_id]

    # Sign-in

    @staticmethod
    def here(request: Request) -> str:
        """This app's public address. Behind a TLS-terminating proxy such as a Cloudflare
        tunnel, the proxy's X-Forwarded-Proto and X-Forwarded-Host give the real one."""
        headers = request.headers
        scheme = headers.get("x-forwarded-proto", "").split(",")[0].strip() or request.url.scheme
        host = headers.get("x-forwarded-host", "").split(",")[0].strip() or request.url.netloc
        return f"{scheme}://{host}/"

    async def person(self, request: Request) -> dict[str, Any] | None:
        return await self.sso.user(request.cookies.get(SSOConfig.COOKIE_NAME))

    def protected(self, handler: Callable[[Request], Awaitable[Response]]):
        """Run ``handler`` only for a signed-in person on this app's allowlist."""

        async def endpoint(request: Request) -> Response:
            user = await self.person(request)
            if user is None:
                return JSONResponse({"error": "Sign in to continue."}, status_code=401)
            if not self.sso.is_allowed(user):
                return JSONResponse({"error": "This account is not authorized."}, status_code=403)
            token = CURRENT_USER.set(user.get("user_id"))
            try:
                return await handler(request)
            finally:
                CURRENT_USER.reset(token)

        return endpoint

    async def session(self, request: Request) -> Response:
        """Tell the browser app who is signed in, with its settings once they are allowed."""
        here = self.here(request)
        user = await self.person(request)
        if user is None:
            return JSONResponse({"status": "signed_out", "login_url": self.sso.login_url(here)})
        logout_url = self.sso.logout_url(here)
        if not self.sso.is_allowed(user):
            return JSONResponse({"status": "forbidden", "email": user.get("email"), "logout_url": logout_url})
        return JSONResponse(
            {
                "status": "signed_in",
                "email": user.get("email"),
                "logout_url": logout_url,
                "settings": {
                    "images_per_page": UIConfig.IMAGES_PER_PAGE,
                    "batch_size": UIConfig.BATCH_SIZE,
                    "max_tags": UIConfig.MAX_TAGS,
                    "name_max_chars": UIConfig.NAME_MAX_CHARS,
                    "caption_max_chars": UIConfig.CAPTION_MAX_CHARS,
                    "upload_types": UIConfig.UPLOAD_TYPES,
                },
            }
        )

    # Vault

    def collection(self, backend_path: str):
        async def endpoint(request: Request) -> Response:
            try:
                response = await self.backend.get(backend_path)
                response.raise_for_status()
            except httpx.HTTPError:
                return JSONResponse({"error": UNAVAILABLE}, status_code=502)
            records = await anyio.to_thread.run_sync(self.files.collection, response.json()["images"])
            return JSONResponse({"images": records}, headers={"Cache-Control": "no-store"})

        return endpoint

    async def thumbnail(self, request: Request) -> Response:
        name = request.path_params["name"]
        try:
            data = await anyio.to_thread.run_sync(self.files.thumbnail, name)
        except (OSError, UnidentifiedImageError):
            return JSONResponse({"error": "Image not found."}, status_code=404)
        return Response(data, media_type="image/webp", headers={"Cache-Control": UIConfig.IMMUTABLE_CACHE_CONTROL})

    async def file(self, request: Request) -> Response:
        """The original image, shown inline or saved under ``?download=<file name>``."""
        try:
            path = self.files.path(request.path_params["name"])
        except FileNotFoundError:
            return JSONResponse({"error": "Image not found."}, status_code=404)
        download = request.query_params.get("download")
        serve = partial(FileResponse, path, headers={"Cache-Control": UIConfig.IMMUTABLE_CACHE_CONTROL})
        return serve(filename=download) if download else serve()

    # Hidden items

    @staticmethod
    def token(request: Request) -> str | None:
        return request.cookies.get(SSOConfig.COOKIE_NAME)

    def unlocked_only(self, handler: Callable[[Request], Awaitable[Response]]):
        """Run ``handler`` only once this session has typed the hidden-items PIN."""

        async def endpoint(request: Request) -> Response:
            if not self.pin.is_unlocked(self.token(request)):
                return JSONResponse({"error": LOCKED, "locked": True}, status_code=423)
            return await handler(request)

        return endpoint

    async def hidden_set(self) -> set[str]:
        response = await self.backend.get("/api/hidden/images")
        response.raise_for_status()
        return {item["name"] for item in response.json().get("images", [])}

    def guarded(self, handler: Callable[[Request], Awaitable[Response]]):
        """Serve a hidden item's files only while unlocked, and never let the browser keep them."""

        async def endpoint(request: Request) -> Response:
            unlocked = self.pin.is_unlocked(self.token(request))
            try:
                hidden = request.path_params["name"] in await self.pin.hidden_names(signed_in_user(), self.hidden_set)
            except httpx.HTTPError:
                if not unlocked:
                    return JSONResponse({"error": UNAVAILABLE}, status_code=502)
                hidden = True
            if hidden and not unlocked:
                return JSONResponse({"error": "Image not found."}, status_code=404)
            response = await handler(request)
            if hidden:
                response.headers["Cache-Control"] = "private, no-store"
            return response

        return endpoint

    async def pin_status(self, request: Request) -> Response:
        try:
            status = await self.pin.status(self.token(request), self.here(request))
        except httpx.HTTPError:
            # Not a 5xx: Cloudflare would show its own error page instead of this message.
            return JSONResponse({"error": SENTIA_AWAY}, status_code=409)
        return JSONResponse(status, headers={"Cache-Control": "no-store"})

    async def unlock(self, request: Request) -> Response:
        try:
            pin = str((await request.json()).get("pin", ""))[:32]
        except (ValueError, AttributeError):
            return JSONResponse({"error": "Send the PIN as JSON."}, status_code=400)
        try:
            status, answer = await self.pin.unlock(self.token(request), pin)
        except httpx.HTTPError:
            # Not a 5xx: Cloudflare would show its own error page instead of this message.
            return JSONResponse({"error": SENTIA_AWAY}, status_code=409)
        return JSONResponse(answer, status_code=status, headers={"Cache-Control": "no-store"})

    async def lock(self, request: Request) -> Response:
        self.pin.lock(self.token(request))
        return JSONResponse({"unlocked": False})

    # Backend

    async def proxy(self, request: Request) -> Response:
        path = request.url.path
        if not any(method == request.method and pattern.fullmatch(path) for method, pattern in PROXIED_ROUTES):
            return JSONResponse({"error": "Not found."}, status_code=404)
        if HIDDEN_CHANGES.fullmatch(path) and not self.pin.is_unlocked(self.token(request)):
            return JSONResponse({"error": LOCKED, "locked": True}, status_code=423)
        body = await request.body()
        if len(body) > UIConfig.MAX_REQUEST_BYTES:
            return JSONResponse({"error": "This request is too large."}, status_code=413)
        timeout = UIConfig.API_TIMEOUT_SECONDS
        if request.method == "POST" and path == "/api/images":
            timeout = UIConfig.UPLOAD_TIMEOUT_SECONDS
        elif BATCH_ROUTES.fullmatch(path):
            timeout = UIConfig.BATCH_TIMEOUT_SECONDS
        try:
            response = await self.backend.request(
                request.method,
                "/".join(quote(part, safe="") for part in path.split("/")),
                content=body,
                headers={"Content-Type": request.headers.get("content-type", "application/json")},
                timeout=timeout,
            )
        except httpx.HTTPError:
            return JSONResponse({"error": UNAVAILABLE}, status_code=502)
        if request.method != "GET":
            # Something may have been hidden, shown, renamed, or deleted.
            self.pin.forget_names(signed_in_user())
        return Response(
            response.content,
            status_code=response.status_code,
            media_type=response.headers.get("content-type"),
        )

    # Application

    @staticmethod
    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    def application(self) -> Starlette:
        app = Starlette(
            routes=[
                Route("/health", self.health),
                # Luna Unia's status page checks every app's UI at Streamlit's health path.
                Route("/_stcore/health", self.health),
                Route("/auth/session", self.session),
                Route("/app/images", self.protected(self.collection("/api/metadata"))),
                Route("/app/hidden/pin", self.protected(self.pin_status)),
                Route("/app/hidden/unlock", self.protected(self.unlock), methods=["POST"]),
                Route("/app/hidden/lock", self.protected(self.lock), methods=["POST"]),
                Route("/app/hidden", self.protected(self.unlocked_only(self.collection("/api/hidden/images")))),
                Route("/app/thumbnails/{name}", self.protected(self.guarded(self.thumbnail))),
                Route("/app/files/{name}", self.protected(self.guarded(self.file))),
                Route("/api/{path:path}", self.protected(self.proxy), methods=["POST", "DELETE", "GET"]),
                Mount("/", StaticApp(directory=UIConfig.STATIC_DIR, html=True, check_dir=False)),
            ]
        )

        async def secure_headers(request: Request, call_next) -> Response:
            response = await call_next(request)
            response.headers["Content-Security-Policy"] = UIConfig.CONTENT_SECURITY_POLICY
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response

        app.add_middleware(BaseHTTPMiddleware, dispatch=secure_headers)
        return app


def create_app() -> Starlette:
    return UIGateway(UIConfig.from_env()).application()
