from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from backend.db import DATABASE_URL, SessionLocal, commit_with_retry
from backend.models import ArchiveEntry
from backend.services.storage import STORAGE_ROOT, normalize_user_id

ARCHIVE_DIR = Path("instance/archives")


def _require_user_id(payload: Dict[str, Any]) -> int:
    raw = payload.get("userId")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError("userId is required")


def build_manual_archive(user_id: int) -> Dict[str, Any]:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"archive_{timestamp}.zip"
    archive_path = ARCHIVE_DIR / filename

    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        if DATABASE_URL.startswith("sqlite"):
            db_path = Path("instance/app.db")
            if db_path.exists():
                archive.write(db_path, arcname="app.db")
        # TODO: for PostgreSQL add pg_dump-based export into archive (future multi-tenant support).
        user_dir = STORAGE_ROOT / normalize_user_id(str(user_id))
        if user_dir.exists():
            for file_path in user_dir.rglob("*"):
                if file_path.is_file():
                    relative_path = file_path.relative_to(STORAGE_ROOT)
                    archive.write(file_path, arcname=f"storage/contracts/{relative_path.as_posix()}")

    session = SessionLocal()
    try:
        entry = ArchiveEntry(user_id=user_id, filename=filename)
        session.add(entry)
        commit_with_retry(session)
    finally:
        session.close()

    return {"downloadUrl": f"/archive/{filename}"}


def handle(action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = _require_user_id(payload)
    if action == "downloadProjectArchive":
        return {"success": True, "data": build_manual_archive(user_id)}
    raise ValueError(f"Unknown archive action: {action}")
