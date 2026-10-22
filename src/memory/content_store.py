from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional
import structlog

logger = structlog.get_logger(__name__)


class BoundedContentStore:
    """Bounded disk-backed store for full extracted page/repo content.

    Avoids duplicating full documents repeatedly inside LangGraph state and memory.
    """

    def __init__(self, base_dir: Optional[str] = None, max_bytes: int = 50 * 1024 * 1024) -> None:
        self.base_dir = Path(base_dir or "storage/content").resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes

    def put(self, canonical_url: str, text: str) -> str:
        """Stores text and returns a content reference hash."""
        if not text:
            return ""

        content_hash = hashlib.sha256(f"{canonical_url}::{text}".encode("utf-8")).hexdigest()
        file_path = self.base_dir / f"{content_hash}.txt"

        try:
            if not file_path.exists():
                file_path.write_text(text, encoding="utf-8")
                self._enforce_bounds()
            return content_hash
        except Exception as e:
            logger.warning("content_store.put_failed", error=str(e), url=canonical_url)
            return ""

    def get(self, ref: str) -> Optional[str]:
        """Retrieves content by reference hash."""
        if not ref:
            return None
        file_path = self.base_dir / f"{ref}.txt"
        if file_path.exists():
            try:
                return file_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning("content_store.get_failed", ref=ref, error=str(e))
        return None

    def _enforce_bounds(self) -> None:
        """Removes oldest files if total size exceeds max_bytes."""
        try:
            files = list(self.base_dir.glob("*.txt"))
            total_size = sum(f.stat().st_size for f in files)
            if total_size <= self.max_bytes:
                return

            # Sort by access/modification time ascending
            files.sort(key=lambda f: f.stat().st_mtime)
            for f in files:
                if total_size <= self.max_bytes:
                    break
                size = f.stat().st_size
                f.unlink(missing_ok=True)
                total_size -= size
        except Exception as e:
            logger.warning("content_store.bounds_failed", error=str(e))


_global_store: Optional[BoundedContentStore] = None


def get_content_store() -> BoundedContentStore:
    global _global_store
    if _global_store is None:
        _global_store = BoundedContentStore()
    return _global_store
