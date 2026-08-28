"""Storage backend abstraction.

Business logic depends on the ``StorageBackend`` protocol below — never on a
specific filesystem layout. The current deployment uses ``LocalStorageBackend``
(local disk under DATA_DIR). The seam exists so media/frames can later move to
S3/GCS/Azure Blob without rewriting search/indexing logic: implement the same
protocol and swap it in the container.

``StorageService`` is the concrete façade that implements this protocol today.
"""
from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """Logical storage operations used by the rest of the application."""

    name: str
    supports_range_streaming: bool

    def read(self, key: str, offset: int = 0, length: int = -1) -> bytes: ...
    def write(self, key: str, data: bytes) -> None: ...
    def stream(self, key: str, chunk_size: int = 1024 * 1024): ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def size(self, key: str) -> int: ...


class LocalStorageBackend:
    """Local-disk backend. Keys are paths relative to DATA_DIR."""

    name = "local"
    supports_range_streaming = True

    def __init__(self, storage_service):
        # StorageService already provides all path-safety and resolution logic;
        # this backend exposes the storage-op subset of that surface.
        self._s = storage_service

    def read(self, key: str, offset: int = 0, length: int = -1) -> bytes:
        path = self._s.resolve_stored(key)
        with open(path, "rb") as f:
            f.seek(offset)
            return f.read(length)

    def write(self, key: str, data: bytes) -> None:
        path = self._s.resolve_stored(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def stream(self, key: str, chunk_size: int = 1024 * 1024):
        path = self._s.resolve_stored(key)
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                yield chunk

    def exists(self, key: str) -> bool:
        try:
            return self._s.resolve_stored(key).exists()
        except Exception:
            return False

    def delete(self, key: str) -> None:
        path = self._s.resolve_stored(key)
        path.unlink(missing_ok=True)

    def size(self, key: str) -> int:
        path = self._s.resolve_stored(key)
        return path.stat().st_size if path.exists() else 0
