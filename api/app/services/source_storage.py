"""Immutable uploaded sources and page previews; see docs/page_storage.md."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

from app.schemas.documents import SourceReference


class SourceStorage:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def save(self, filename: str, data: bytes, content_type: str | None) -> UUID:
        source_id = uuid4()
        folder = self.root / str(source_id)
        folder.mkdir(parents=True)
        (folder / "original").write_bytes(data)
        (folder / "metadata.json").write_text(
            json.dumps(
                {"filename": Path(filename).name, "content_type": content_type or "application/octet-stream"},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return source_id

    def reference(self, source_id: UUID, page: int, count: int, preview: bytes | None = None) -> SourceReference:
        folder = self.root / str(source_id)
        if preview:
            (folder / f"page-{page}.png").write_bytes(preview)
        metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        return SourceReference(
            source_id=source_id,
            filename=metadata["filename"],
            page_number=page,
            page_count=count,
            preview_url=f"/sources/{source_id}/pages/{page}" if preview else None,
            download_url=f"/sources/{source_id}",
        )

    def original(self, source_id: UUID) -> tuple[Path, dict]:
        folder = self.root / str(source_id)
        return folder / "original", json.loads((folder / "metadata.json").read_text(encoding="utf-8"))

    def preview(self, source_id: UUID, page: int) -> Path:
        if page < 1:
            raise FileNotFoundError("Invalid page")
        return self.root / str(source_id) / f"page-{page}.png"
