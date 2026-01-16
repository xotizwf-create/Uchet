from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import func, select

from backend.db import SessionLocal, commit_with_retry
from backend.models import Contract, ContractItem
from backend.services.utils import format_date, normalize_text, parse_date, to_float, unique_preserve


def _require_user_id(payload: Dict[str, Any]) -> int:
    raw = payload.get("userId")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError("userId is required")


def _contract_to_dict(contract: Contract) -> Dict[str, Any]:
    items = [
        {
            "item": item.item,
            "qty": item.qty,
            "planQty": item.plan_qty,
            "planDate": format_date(item.plan_date),
            "dateFact": format_date(item.date_fact),
            "delivered": item.delivered,
        }
        for item in sorted(contract.items, key=lambda it: it.position)
    ]

    first_item = items[0] if items else {}

    return {
        "id": contract.id,
        "rowNumber": contract.order_index,
        "forceDone": bool(contract.force_done),
        "date": format_date(contract.date),
        "deadline": format_date(contract.deadline),
        "supplier": contract.supplier or "",
        "org": contract.org or "",
        "dateFact": first_item.get("dateFact") or format_date(contract.date_fact),
        "docsSent": bool(contract.docs_sent),
        "number": contract.number or "",
        "linkUrl": contract.link_url or "",
        "item": first_item.get("item") or contract.item or "",
        "qty": first_item.get("qty") if first_item.get("qty") is not None else contract.qty,
        "planQty": first_item.get("planQty") if first_item.get("planQty") is not None else contract.plan_qty,
        "planDate": first_item.get("planDate") or format_date(contract.plan_date),
        "delivered": first_item.get("delivered") if first_item.get("delivered") is not None else contract.delivered,
        "items": items,
    }


def _normalize_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    if not items:
        items = [
            {
                "item": payload.get("item", ""),
                "qty": payload.get("qty"),
                "planQty": payload.get("planQty"),
                "planDate": payload.get("planDate"),
                "dateFact": payload.get("dateFact"),
                "delivered": payload.get("delivered"),
            }
        ]

    normalized = []
    for item in items:
        if not item:
            continue
        normalized_item = {
            "item": normalize_text(item.get("item")),
            "qty": to_float(item.get("qty")),
            "planQty": to_float(item.get("planQty")),
            "planDate": parse_date(item.get("planDate")),
            "dateFact": parse_date(item.get("dateFact")),
            "delivered": to_float(item.get("delivered")),
        }
        if any(
            [
                normalized_item["item"],
                normalized_item["qty"],
                normalized_item["planQty"],
                normalized_item["planDate"],
                normalized_item["dateFact"],
                normalized_item["delivered"],
            ]
        ):
            normalized.append(normalized_item)

    return normalized


def list_contracts(user_id: int) -> List[Dict[str, Any]]:
    session = SessionLocal()
    try:
        contracts = (
            session.execute(select(Contract).where(Contract.user_id == user_id).order_by(Contract.order_index))
            .scalars()
            .all()
        )
        return [_contract_to_dict(contract) for contract in contracts]
    finally:
        session.close()


def get_contract(contract_id: str, user_id: int) -> Dict[str, Any] | None:
    session = SessionLocal()
    try:
        contract = session.execute(
            select(Contract).where(Contract.id == contract_id, Contract.user_id == user_id)
        ).scalar_one_or_none()
        if not contract:
            return None
        return _contract_to_dict(contract)
    finally:
        session.close()


def _next_order_index(session, user_id: int) -> int:
    max_index = session.execute(select(func.max(Contract.order_index)).where(Contract.user_id == user_id)).scalar()
    return (max_index or 0) + 1


def _shift_order_indexes(session, user_id: int, start_index: int, shift: int) -> None:
    if shift == 0:
        return
    contracts = session.execute(
        select(Contract)
        .where(Contract.user_id == user_id, Contract.order_index > start_index)
        .order_by(Contract.order_index.desc())
    ).scalars()
    for contract in contracts:
        contract.order_index += shift


def create_contract(payload: Dict[str, Any], user_id: int) -> Dict[str, Any]:
    session = SessionLocal()
    try:
        items = _normalize_items(payload)
        main_item = items[0] if items else {}

        insert_after_id = payload.get("insertAfterId")
        if insert_after_id:
            after_contract = session.execute(
                select(Contract).where(Contract.id == insert_after_id, Contract.user_id == user_id)
            ).scalar_one_or_none()
            if after_contract:
                order_index = after_contract.order_index + 1
                _shift_order_indexes(session, user_id, after_contract.order_index, 1)
            else:
                order_index = _next_order_index(session, user_id)
        else:
            order_index = _next_order_index(session, user_id)

        contract = Contract(
            user_id=user_id,
            order_index=order_index,
            force_done=bool(payload.get("forceDone")),
            date=parse_date(payload.get("date")),
            deadline=parse_date(payload.get("deadline")),
            supplier=normalize_text(payload.get("supplier")),
            org=normalize_text(payload.get("org")),
            date_fact=parse_date(main_item.get("dateFact") or payload.get("dateFact")),
            docs_sent=bool(payload.get("docsSent")),
            number=normalize_text(payload.get("number")),
            link_url=normalize_text(payload.get("linkUrl")),
            item=normalize_text(main_item.get("item") or payload.get("item")),
            qty=to_float(main_item.get("qty") or payload.get("qty")),
            plan_qty=to_float(main_item.get("planQty") or payload.get("planQty")),
            plan_date=parse_date(main_item.get("planDate") or payload.get("planDate")),
            delivered=to_float(main_item.get("delivered") or payload.get("delivered")),
        )

        session.add(contract)
        session.flush()

        for idx, item in enumerate(items):
            session.add(
                ContractItem(
                    contract_id=contract.id,
                    position=idx,
                    item=item.get("item", ""),
                    qty=item.get("qty", 0),
                    plan_qty=item.get("planQty", 0),
                    plan_date=item.get("planDate"),
                    date_fact=item.get("dateFact"),
                    delivered=item.get("delivered", 0),
                )
            )

        commit_with_retry(session)
        return _contract_to_dict(contract)
    finally:
        session.close()


def create_many(payload: Dict[str, Any], user_id: int) -> List[Dict[str, Any]]:
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    if not items:
        return []

    session = SessionLocal()
    try:
        after_id = payload.get("afterId")
        if after_id:
            after_contract = session.execute(
                select(Contract).where(Contract.id == after_id, Contract.user_id == user_id)
            ).scalar_one_or_none()
            start_index = after_contract.order_index if after_contract else _next_order_index(session, user_id)
        else:
            start_index = _next_order_index(session, user_id) - 1

        _shift_order_indexes(session, user_id, start_index, len(items))

        created = []
        for offset, item in enumerate(items, start=1):
            normalized_items = _normalize_items(item)
            main_item = normalized_items[0] if normalized_items else {}

            contract = Contract(
                user_id=user_id,
                order_index=start_index + offset,
                force_done=bool(item.get("forceDone")),
                date=parse_date(item.get("date")),
                deadline=parse_date(item.get("deadline")),
                supplier=normalize_text(item.get("supplier")),
                org=normalize_text(item.get("org")),
                date_fact=parse_date(main_item.get("dateFact") or item.get("dateFact")),
                docs_sent=bool(item.get("docsSent")),
                number=normalize_text(item.get("number")),
                link_url=normalize_text(item.get("linkUrl")),
                item=normalize_text(main_item.get("item") or item.get("item")),
                qty=to_float(main_item.get("qty") or item.get("qty")),
                plan_qty=to_float(main_item.get("planQty") or item.get("planQty")),
                plan_date=parse_date(main_item.get("planDate") or item.get("planDate")),
                delivered=to_float(main_item.get("delivered") or item.get("delivered")),
            )
            session.add(contract)
            session.flush()

            for idx, payload_item in enumerate(normalized_items):
                session.add(
                    ContractItem(
                        contract_id=contract.id,
                        position=idx,
                        item=payload_item.get("item", ""),
                        qty=payload_item.get("qty", 0),
                        plan_qty=payload_item.get("planQty", 0),
                        plan_date=payload_item.get("planDate"),
                        date_fact=payload_item.get("dateFact"),
                        delivered=payload_item.get("delivered", 0),
                    )
                )

            created.append(_contract_to_dict(contract))

        commit_with_retry(session)
        return created
    finally:
        session.close()


def update_contract(payload: Dict[str, Any], user_id: int) -> Dict[str, Any]:
    contract_id = payload.get("id")
    if not contract_id:
        raise ValueError("id is required for update")

    session = SessionLocal()
    try:
        contract = session.execute(
            select(Contract).where(Contract.id == contract_id, Contract.user_id == user_id)
        ).scalar_one_or_none()
        if not contract:
            raise ValueError("Contract with id %s not found" % contract_id)

        items = _normalize_items(payload)
        main_item = items[0] if items else {}

        contract.force_done = bool(payload.get("forceDone"))
        contract.date = parse_date(payload.get("date"))
        contract.deadline = parse_date(payload.get("deadline"))
        contract.supplier = normalize_text(payload.get("supplier"))
        contract.org = normalize_text(payload.get("org"))
        contract.date_fact = parse_date(main_item.get("dateFact") or payload.get("dateFact"))
        contract.docs_sent = bool(payload.get("docsSent"))
        contract.number = normalize_text(payload.get("number"))
        contract.link_url = normalize_text(payload.get("linkUrl"))
        contract.item = normalize_text(main_item.get("item") or payload.get("item"))
        contract.qty = to_float(main_item.get("qty") or payload.get("qty"))
        contract.plan_qty = to_float(main_item.get("planQty") or payload.get("planQty"))
        contract.plan_date = parse_date(main_item.get("planDate") or payload.get("planDate"))
        contract.delivered = to_float(main_item.get("delivered") or payload.get("delivered"))

        contract.items.clear()
        for idx, item in enumerate(items):
            contract.items.append(
                ContractItem(
                    position=idx,
                    item=item.get("item", ""),
                    qty=item.get("qty", 0),
                    plan_qty=item.get("planQty", 0),
                    plan_date=item.get("planDate"),
                    date_fact=item.get("dateFact"),
                    delivered=item.get("delivered", 0),
                )
            )

        commit_with_retry(session)
        return _contract_to_dict(contract)
    finally:
        session.close()


def delete_contract(contract_id: str, user_id: int) -> None:
    if not contract_id:
        raise ValueError("id is required for delete")

    session = SessionLocal()
    try:
        contract = session.execute(
            select(Contract).where(Contract.id == contract_id, Contract.user_id == user_id)
        ).scalar_one_or_none()
        if not contract:
            raise ValueError("Contract with id %s not found" % contract_id)
        session.delete(contract)
        commit_with_retry(session)
    finally:
        session.close()


def delete_many(ids: List[str], user_id: int) -> None:
    if not ids:
        return
    session = SessionLocal()
    try:
        contracts = (
            session.execute(select(Contract).where(Contract.id.in_(ids), Contract.user_id == user_id))
            .scalars()
            .all()
        )
        for contract in contracts:
            session.delete(contract)
        commit_with_retry(session)
    finally:
        session.close()


def list_references(user_id: int) -> Dict[str, List[str]]:
    session = SessionLocal()
    try:
        orgs = [
            normalize_text(row[0])
            for row in session.execute(select(Contract.org).where(Contract.user_id == user_id)).all()
            if row[0]
        ]
        suppliers = [
            normalize_text(row[0])
            for row in session.execute(select(Contract.supplier).where(Contract.user_id == user_id)).all()
            if row[0]
        ]
        orgs = [org for org in orgs if org and not org.replace(".", "").isdigit()]
        orgs_unique = unique_preserve(orgs)
        suppliers_unique = unique_preserve(suppliers)
        return {
            "orgs": orgs_unique,
            "orgsS": orgs_unique,
            "suppliers": suppliers_unique,
        }
    finally:
        session.close()


def handle(action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = _require_user_id(payload)
    if action == "list":
        return {"success": True, "data": list_contracts(user_id)}
    if action == "refs":
        return {"success": True, "data": list_references(user_id)}
    if action == "get":
        return {"success": True, "data": get_contract(payload.get("id"), user_id)}
    if action == "create":
        return {"success": True, "data": create_contract(payload, user_id)}
    if action == "createMany":
        return {"success": True, "data": create_many(payload, user_id)}
    if action == "update":
        return {"success": True, "data": update_contract(payload, user_id)}
    if action == "delete":
        delete_contract(payload.get("id"), user_id)
        return {"success": True}
    if action == "deleteMany":
        delete_many(payload.get("ids") or [], user_id)
        return {"success": True}
    raise ValueError(f"Unknown contracts action: {action}")
