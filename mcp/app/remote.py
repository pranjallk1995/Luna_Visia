"""Resolve chat-supplied image sources into image bytes."""

import ipaddress
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from .media import ImagePayloadDecoder


@dataclass(frozen=True)
class ImageSource:
    """Raw image bytes plus a filename hint from the source, when one exists."""

    data: bytes
    suggested_name: str | None = None


class ImageSourceResolver:
    """Accept Base64, a data URL, a public HTTPS URL, or a ChatGPT file reference.

    Downloads are SSRF-guarded: every hop must be HTTPS and resolve only to
    public addresses.
    """

    LOCAL_PATH_PREFIXES = ("file:", "sandbox:", "/", "./", "~")
    LOCAL_PATH_MESSAGE = (
        "Local file paths are not reachable by the Luna Visia server. Pass the "
        "attachment's file reference or a public HTTPS URL, or call "
        "create_upload_link and send the file's bytes to the returned URL."
    )

    def __init__(self, decoder: ImagePayloadDecoder, max_bytes: int, max_redirects: int) -> None:
        self.decoder = decoder
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects

    def resolve(
        self,
        content_base64: str | None = None,
        image_url: str | None = None,
        file: dict[str, Any] | None = None,
    ) -> ImageSource:
        """Return image bytes from exactly one supported source."""
        supplied = [
            value
            for value in (content_base64, image_url, file)
            if value is not None and (not isinstance(value, str) or value.strip())
        ]
        if len(supplied) != 1:
            raise ValueError("Provide exactly one of file, image_url, or content_base64.")
        if file is not None:
            return self._file(file)
        if image_url is not None:
            return self._url(image_url.strip())
        return ImageSource(self.decoder.decode_base64(content_base64))

    def _file(self, file: dict[str, Any]) -> ImageSource:
        """Download a client file reference such as ChatGPT's ``{download_url, file_id}``."""
        if not isinstance(file, dict) or not isinstance(file.get("download_url"), str):
            raise ValueError("file must be an object with a download_url.")
        name = file.get("file_name") or file.get("name") or file.get("filename")
        source = self._url(file["download_url"])
        return ImageSource(source.data, name if isinstance(name, str) else source.suggested_name)

    def _url(self, url: str) -> ImageSource:
        if url[:5].casefold() == "data:":
            return ImageSource(self.decoder.decode_base64(url))
        if url.casefold().startswith(self.LOCAL_PATH_PREFIXES):
            raise ValueError(self.LOCAL_PATH_MESSAGE)
        data, final_url = self._download(url)
        return ImageSource(data, unquote(urlparse(final_url).path.rsplit("/", 1)[-1]) or None)

    @staticmethod
    def validate_public_https_url(url: str) -> None:
        """Reject non-HTTPS and private-network upload sources."""
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("image_url must be a public HTTPS URL or image data URL.")
        try:
            addresses = {
                entry[4][0] for entry in socket.getaddrinfo(parsed.hostname, parsed.port or 443)
            }
        except socket.gaierror as error:
            raise ValueError("image_url hostname could not be resolved.") from error
        if not addresses or any(not ipaddress.ip_address(value).is_global for value in addresses):
            raise ValueError("image_url must resolve only to public internet addresses.")

    def _download(self, url: str) -> tuple[bytes, str]:
        """Follow a bounded number of validated redirects and stream the body.

        The content type is not trusted: file hosts often send images as
        ``application/octet-stream``, so the bytes are validated afterwards.
        """
        current = url
        with httpx.Client(timeout=30.0, follow_redirects=False) as client:
            for redirect_count in range(self.max_redirects + 1):
                self.validate_public_https_url(current)
                with client.stream("GET", current) as response:
                    if response.is_redirect:
                        if redirect_count == self.max_redirects:
                            raise ValueError("image_url redirected too many times.")
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("image_url returned an invalid redirect.")
                        current = str(response.url.join(location))
                        continue
                    response.raise_for_status()
                    if response.headers.get("content-type", "").casefold().startswith("text/html"):
                        raise ValueError("image_url returned a web page, not an image file.")
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > self.max_bytes:
                            raise ValueError("Image content exceeds the 50 MiB limit.")
                    if not data:
                        raise ValueError("image_url returned an empty image.")
                    return bytes(data), current
        raise ValueError("image_url could not be downloaded.")
