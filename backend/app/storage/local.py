from pathlib import Path

from app.config import settings


class LocalStorage:
    """Filesystem storage. Same interface an S3 implementation will expose."""

    def __init__(self, base: str | None = None):
        self.base = Path(base or settings.storage_dir)

    def put(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def _path(self, key: str) -> Path:
        path = (self.base / key).resolve()
        if not path.is_relative_to(self.base.resolve()):
            raise ValueError("invalid storage key")
        return path
