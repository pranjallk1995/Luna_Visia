"""UI-facing client for Luna Visia's HTTP routes."""

from typing import Any
from urllib.parse import quote

import httpx

from config import UIConfig

class LunaVisiaAPI:
    """Keep backend routes and HTTP details out of Streamlit views."""

    def __init__(self, config: UIConfig) -> None:
        self.base_url = config.api_url
        self.headers = {"Authorization": f"Bearer {config.api_token}"}

    def request(
        self,
        method: str,
        path: str,
        *,
        timeout: float = 30.0,
        **kwargs: Any,
    ) -> httpx.Response:
        response = httpx.request(
            method, f"{self.base_url}{path}", headers=self.headers, timeout=timeout, **kwargs
        )
        response.raise_for_status()
        return response

    @staticmethod
    def error_message(error: httpx.HTTPError, fallback: str) -> str:
        """Prefer the backend's ``error`` text over a generic message."""
        if isinstance(error, httpx.HTTPStatusError):
            try:
                return error.response.json().get("error", fallback)
            except ValueError:
                return fallback
        return fallback

    @staticmethod
    def metadata_change(
        caption: str | None = None,
        add_tags: list[str] | None = None,
        remove_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Build a metadata-edit payload containing only the requested changes."""
        change = {"caption": caption, "add_tags": add_tags, "remove_tags": remove_tags}
        return {key: value for key, value in change.items() if value is not None}

    @staticmethod
    def image_path(prefix: str, name: str, suffix: str = "") -> str:
        return f"{prefix}/{quote(name, safe='')}{suffix}"

    # Visible images

    def metadata(self) -> list[dict[str, Any]]:
        return self.request("GET", "/api/metadata", timeout=10.0).json()["images"]

    def upload_image(self, name: str, content_base64: str) -> dict[str, Any]:
        return self.request(
            "POST",
            "/api/images",
            json={"name": name, "content_base64": content_base64},
            timeout=360.0,
        ).json()

    def delete_image(self, name: str) -> None:
        self.request("DELETE", self.image_path("/api/images", name))

    def rename_image(self, name: str, new_name: str) -> dict[str, Any]:
        return self.request(
            "POST", self.image_path("/api/images", name, "/rename"), json={"new_name": new_name}
        ).json()

    def update_image_metadata(self, name: str, payload: dict) -> dict[str, Any]:
        return self.request(
            "POST", self.image_path("/api/images", name, "/metadata"), json=payload
        ).json()

    def favorites(self) -> list[dict[str, Any]]:
        return self.request("GET", "/api/favorites", timeout=10.0).json()["images"]

    def set_favorites(self, names: list[str], favorite: bool) -> None:
        self.request("POST", "/api/favorites", json={"names": names, "favorite": favorite})

    # Hidden images

    def hidden_images(self) -> list[dict[str, Any]]:
        return self.request("GET", "/api/hidden/images", timeout=10.0).json()["images"]

    def hide_images(self, names: list[str]) -> None:
        self.request("POST", "/api/hidden/hide", json={"names": names})

    def restore_hidden_images(self, names: list[str]) -> None:
        self.request("POST", "/api/hidden/restore", json={"names": names})

    def delete_hidden_images(self, names: list[str]) -> None:
        self.request("POST", "/api/hidden/delete", json={"names": names})

    def update_hidden_metadata(self, name: str, payload: dict) -> dict[str, Any]:
        return self.request(
            "POST",
            self.image_path("/api/hidden/images", name, "/metadata"),
            json=payload,
        ).json()
