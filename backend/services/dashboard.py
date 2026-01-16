from __future__ import annotations

import os
import uuid
import mimetypes
from datetime import date, datetime, timedelta
from typing import Any, Dict, List

from sqlalchemy import select

from backend.db import SessionLocal, commit_with_retry
from backend.models import Contract, DriveFile
from backend.services.storage import ensure_user_storage_dir, normalize_user_id
from backend.services.utils import decode_base64, format_date, normalize_text, parse_date, to_float
from backend.services.warehouse import balances_by_date


def _calc_status(contract: Contract) -> str:
    qty = to_float(contract.qty)
    delivered = to_float(contract.delivered)
    has_fact = contract.date_fact is not None
    docs_sent = bool(contract.docs_sent)
    if contract.force_done or has_fact or (qty > 0 and qty == delivered and docs_sent):
        return "done"
    return "inwork"


def _extract_contract_items(contract: Contract) -> List[Dict[str, Any]]:
    items = [
        {
            "item": item.item,
            "planQty": item.plan_qty,
            "planDate": item.plan_date,
            "qty": item.qty,
            "dateFact": item.date_fact,
            "delivered": item.delivered,
        }
        for item in contract.items
    ]
    if not items:
        items = [
            {
                "item": contract.item,
                "planQty": contract.plan_qty,
                "planDate": contract.plan_date,
                "qty": contract.qty,
                "dateFact": contract.date_fact,
                "delivered": contract.delivered,
            }
        ]
    return items


def _get_week_bounds(input_date: date) -> tuple[date, date]:
    start = input_date - timedelta(days=input_date.weekday())
    end = start + timedelta(days=6)
    return start, end


def _require_user_id(payload: Dict[str, Any]) -> int:
    raw = payload.get("userId")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError("userId is required")


def build_overview(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = _require_user_id(payload)
    storage_user_id = normalize_user_id(payload.get("userId"))
    session = SessionLocal()
    try:
        today = date.today()
        period_start = parse_date(payload.get("fromDate"))
        period_end = parse_date(payload.get("toDate"))

        if period_start is None and period_end:
            period_start, _ = _get_week_bounds(period_end)
        if period_end is None and period_start:
            _, period_end = _get_week_bounds(period_start)
        if period_start is None:
            period_start, period_end = _get_week_bounds(today)

        contracts = (
            session.execute(select(Contract).where(Contract.user_id == user_id).order_by(Contract.order_index))
            .scalars()
            .all()
        )
        done_count = 0
        in_work_count = 0
        plans: List[Dict[str, Any]] = []

        for contract in contracts:
            status = _calc_status(contract)
            if status == "done":
                done_count += 1
            else:
                in_work_count += 1

            items = _extract_contract_items(contract)
            for item in items:
                plan_qty = to_float(item.get("planQty"))
                plan_date = parse_date(item.get("planDate"))
                if plan_qty > 0 and plan_date:
                    if period_start <= plan_date <= period_end:
                        plans.append(
                            {
                                "contractId": contract.id,
                                "contractNumber": contract.number or "",
                                "org": contract.org or "",
                                "date": format_date(plan_date),
                                "item": item.get("item") or contract.item or "",
                                "qty": plan_qty,
                            }
                        )

        plans.sort(
            key=lambda row: (
                parse_date(row.get("date")) or date.min,
                (row.get("org") or "").lower(),
                (row.get("item") or "").lower(),
            )
        )

        balances = [
            b for b in balances_by_date(format_date(today), user_id) if to_float(b.get("qty")) > 0
        ]

        return {
            "counts": {"done": done_count, "inwork": in_work_count},
            "plans": plans,
            "balances": balances,
            "drive": {"files": list_drive_files(storage_user_id)},
            "period": {"from": format_date(period_start), "to": format_date(period_end)},
        }
    finally:
        session.close()


def list_drive_files(user_id: str) -> List[Dict[str, Any]]:
    session = SessionLocal()
    try:
        files = (
            session.execute(
                select(DriveFile).where(DriveFile.user_id == user_id).order_by(DriveFile.created_at.desc())
            )
            .scalars()
            .all()
        )
        result = []
        for file in files:
            result.append(
                {
                    "id": file.id,
                    "name": file.name,
                    "url": f"/drive/{file.id}?userId={user_id}",
                    "created": file.created_at.date().isoformat(),
                    "updated": file.updated_at.date().isoformat(),
                }
            )
        return result
    finally:
        session.close()


def upload_drive_file(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = normalize_user_id(payload.get("userId"))
    drive_dir = ensure_user_storage_dir(user_id)
    name = normalize_text(payload.get("name")) or "file"
    mime_type = normalize_text(payload.get("mimeType")) or "application/octet-stream"
    content = payload.get("content") or ""
    raw = decode_base64(content)
    storage_name = f"{uuid.uuid4()}_{name}"
    path = drive_dir / storage_name
    path.write_bytes(raw)

    session = SessionLocal()
    try:
        drive_file = DriveFile(user_id=user_id, name=name, storage_name=storage_name, mime_type=mime_type)
        session.add(drive_file)
        commit_with_retry(session)
        return {"files": list_drive_files(user_id)}
    finally:
        session.close()


def delete_drive_file(file_id: str, user_id: str) -> Dict[str, Any]:
    session = SessionLocal()
    try:
        file = session.execute(
            select(DriveFile).where(DriveFile.id == file_id, DriveFile.user_id == user_id)
        ).scalar_one_or_none()
        if not file:
            return {"files": list_drive_files(user_id)}
        path = ensure_user_storage_dir(user_id) / file.storage_name
        if path.exists():
            path.unlink()
        session.delete(file)
        commit_with_retry(session)
        return {"files": list_drive_files(user_id)}
    finally:
        session.close()


def process_contracts(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = normalize_user_id(payload.get("userId"))
    drive_dir = ensure_user_storage_dir(user_id)
    session = SessionLocal()
    try:
        stored_names = set(
            session.execute(select(DriveFile.storage_name).where(DriveFile.user_id == user_id)).scalars().all()
        )
        added = []
        processed = 0
        for file_path in drive_dir.iterdir():
            if not file_path.is_file():
                continue
            processed += 1
            if file_path.name in stored_names:
                continue
            name = normalize_text(file_path.name) or "file"
            storage_name = f"{uuid.uuid4()}_{name}"
            target_path = drive_dir / storage_name
            file_path.rename(target_path)
            mime_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
            session.add(
                DriveFile(
                    user_id=user_id,
                    name=name,
                    storage_name=storage_name,
                    mime_type=mime_type,
                )
            )
            added.append({"title": name})
        commit_with_retry(session)
        return {
            "added": added,
            "processed": processed,
            "skipped": False,
            "remaining": 0,
        }
    finally:
        session.close()


def handle(action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    storage_user_id = normalize_user_id(payload.get("userId"))
    if action == "overview":
        return {"success": True, "data": build_overview(payload)}
    if action == "driveList":
        return {"success": True, "data": {"files": list_drive_files(storage_user_id)}}
    if action == "uploadDriveFile":
        return {"success": True, "data": upload_drive_file(payload)}
    if action == "deleteDriveFile":
        return {"success": True, "data": delete_drive_file(payload.get("id"), storage_user_id)}
    if action == "processContracts":
        return {"success": True, "data": process_contracts(payload)}
    raise ValueError(f"Unknown dashboard action: {action}")
