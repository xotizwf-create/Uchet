from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import select

from backend.db import SessionLocal
from backend.models import PriceItem


def _require_user_id(payload: Dict[str, Any]) -> int:
    raw = payload.get("userId")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError("userId is required")


def list_prices(user_id: int) -> List[Dict[str, Any]]:
    session = SessionLocal()
    try:
        items = session.execute(select(PriceItem).where(PriceItem.user_id == user_id)).scalars().all()
        return [
            {
                "code": item.code,
                "name": item.name,
                "priceNoVat": item.price_no_vat,
                "priceWithVat": item.price_with_vat,
                "note": item.note,
            }
            for item in items
        ]
    finally:
        session.close()


def handle(action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = _require_user_id(payload)
    if action == "list":
        return {"success": True, "data": list_prices(user_id)}
    raise ValueError(f"Unknown pricelist action: {action}")
