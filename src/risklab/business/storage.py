"""Small content-addressed local object store: private, immutable, checksum verified."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path


class StorageError(ValueError):
    """Safe storage failure; do not expose host paths to clients."""


class LocalStorage:
    def __init__(self, root: Path) -> None:
        # The root is trusted operator configuration; object keys are not paths.
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def read(self, key: str, *, max_bytes: int = 5_000_000) -> bytes:
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise StorageError("Invalid object key")
        try:
            descriptor = os.open(self.root / key, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                data = stream.read(max_bytes + 1)
        except OSError:
            raise StorageError("Object unavailable") from None
        if len(data) > max_bytes or hashlib.sha256(data).hexdigest() != key:
            raise StorageError("Object integrity check failed")
        return data

    def put(self, data: bytes) -> str:
        """Link a fully-written temp file without replacing an existing immutable object."""
        key = hashlib.sha256(data).hexdigest()
        descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=self.root)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                os.fchmod(stream.fileno(), 0o400)
            try:
                os.link(temporary, self.root / key)
            except FileExistsError:
                if self.read(key, max_bytes=len(data)) != data:
                    raise StorageError("Object collision") from None
            # Persist directory entries before the caller commits a DB reference.
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return key
