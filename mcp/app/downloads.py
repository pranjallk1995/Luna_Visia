"""Short-lived ZIP archives served through unguessable download links."""

import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .media import ImageArchive


@dataclass(frozen=True)
class DownloadTicket:
    """One prepared archive and the capability token that unlocks it."""

    token: str
    path: Path
    file_name: str
    image_count: int
    size_bytes: int
    expires_at: float

    @property
    def expires_at_iso(self) -> str:
        return datetime.fromtimestamp(self.expires_at, timezone.utc).isoformat(timespec="seconds")


class DownloadStore:
    """Write archives to a private directory and expire them after a TTL.

    Tickets live in memory, so a restart invalidates outstanding links; the
    store's ``*.zip`` files are removed on start to drop archives orphaned by
    that restart.
    """

    FILE_NAME = "luna-visia-images.zip"

    def __init__(self, directory: Path, ttl_seconds: int) -> None:
        self.directory = directory
        self.ttl_seconds = ttl_seconds
        self._tickets: dict[str, DownloadTicket] = {}
        self._lock = threading.Lock()
        self.directory.mkdir(parents=True, exist_ok=True)
        for orphan in self.directory.glob("*.zip"):
            orphan.unlink(missing_ok=True)

    def create(self, paths: list[Path]) -> DownloadTicket:
        """Archive ``paths`` and return a ticket valid for the TTL."""
        self.purge_expired()
        token = secrets.token_urlsafe(32)
        destination = self.directory / f"{token}.zip"
        try:
            ImageArchive.write(paths, destination)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        ticket = DownloadTicket(
            token=token,
            path=destination,
            file_name=self.FILE_NAME,
            image_count=len(paths),
            size_bytes=destination.stat().st_size,
            expires_at=time.time() + self.ttl_seconds,
        )
        with self._lock:
            self._tickets[token] = ticket
        return ticket

    def resolve(self, token: str) -> DownloadTicket:
        """Return a live ticket or raise ``FileNotFoundError``."""
        with self._lock:
            ticket = self._tickets.get(token)
        if ticket is None or ticket.expires_at <= time.time() or not ticket.path.is_file():
            self.purge_expired()
            raise FileNotFoundError("This download link has expired or does not exist.")
        return ticket

    def purge_expired(self) -> None:
        now = time.time()
        with self._lock:
            expired = [token for token, ticket in self._tickets.items() if ticket.expires_at <= now]
            tickets = [self._tickets.pop(token) for token in expired]
        for ticket in tickets:
            ticket.path.unlink(missing_ok=True)
