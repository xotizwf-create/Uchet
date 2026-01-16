from __future__ import annotations

import re
from pathlib import Path

from backend.services.utils import normalize_text

STORAGE_ROOT = Path("instance/storage/contracts")
DEFAULT_USER_ID = "default"
USER_ID_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def normalize_user_id(value: str | None) -> str:
    user_id = normalize_text(value)
    if not user_id:
        return DEFAULT_USER_ID
    cleaned = USER_ID_RE.sub("_", user_id)
    return cleaned or DEFAULT_USER_ID


def get_user_storage_dir(user_id: str) -> Path:
    return STORAGE_ROOT / user_id


def ensure_user_storage_dir(user_id: str) -> Path:
    path = get_user_storage_dir(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path
