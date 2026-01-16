from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List

from sqlalchemy import select

from backend.db import SessionLocal, commit_with_retry
from backend.models import Contract, WarehouseExpense, WarehouseIncome, WarehouseItem
from backend.services.utils import format_date, normalize_text, parse_date, to_float

EXPENSES_CUTOFF = date(2025, 12, 6)


def _require_user_id(payload: Dict[str, Any]) -> int:
    raw = payload.get("userId")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError("userId is required")


def _normalize_item_name(value: Any) -> str:
    return normalize_text(value)


def _normalize_in_stock(value: Any) -> bool:
    if value in (None, ""):
        return True
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "yes", "1"}:
        return True
    if text in {"false", "no", "0"}:
        return False
    return True


def _get_items_index(session, user_id: int) -> Dict[str, WarehouseItem]:
    items = session.execute(select(WarehouseItem).where(WarehouseItem.user_id == user_id)).scalars().all()
    return {item.name: item for item in items}


def list_items(user_id: int) -> List[Dict[str, Any]]:
    session = SessionLocal()
    try:
        items = session.execute(select(WarehouseItem).where(WarehouseItem.user_id == user_id)).scalars().all()
        return [
            {
                "id": item.id,
                "name": item.name,
                "unit": item.unit,
                "active": bool(item.active),
            }
            for item in items
        ]
    finally:
        session.close()


def create_item(payload: Dict[str, Any], user_id: int) -> None:
    name = _normalize_item_name(payload.get("name"))
    if not name:
        raise ValueError("Item name is required")
    session = SessionLocal()
    try:
        session.add(
            WarehouseItem(
                user_id=user_id,
                name=name,
                unit=normalize_text(payload.get("unit")),
                active=True,
            )
        )
        commit_with_retry(session)
    finally:
        session.close()


def update_item(payload: Dict[str, Any], user_id: int) -> None:
    item_id = payload.get("id")
    if not item_id:
        raise ValueError("id is required for updateItem")
    session = SessionLocal()
    try:
        item = session.execute(
            select(WarehouseItem).where(WarehouseItem.id == item_id, WarehouseItem.user_id == user_id)
        ).scalar_one_or_none()
        if not item:
            raise ValueError("Item not found")
        name = _normalize_item_name(payload.get("name"))
        if not name:
            raise ValueError("Item name is required")
        item.name = name
        item.unit = normalize_text(payload.get("unit"))
        item.active = bool(payload.get("active", True))
        commit_with_retry(session)
    finally:
        session.close()


def delete_item(item_id: str, user_id: int) -> None:
    if not item_id:
        raise ValueError("id is required for deleteItem")
    session = SessionLocal()
    try:
        item = session.execute(
            select(WarehouseItem).where(WarehouseItem.id == item_id, WarehouseItem.user_id == user_id)
        ).scalar_one_or_none()
        if not item:
            raise ValueError("Item not found")
        session.delete(item)
        commit_with_retry(session)
    finally:
        session.close()


def get_item(item_id: str, user_id: int) -> Dict[str, Any] | None:
    session = SessionLocal()
    try:
        item = session.execute(
            select(WarehouseItem).where(WarehouseItem.id == item_id, WarehouseItem.user_id == user_id)
        ).scalar_one_or_none()
        if not item:
            return None
        return {
            "id": item.id,
            "name": item.name,
            "unit": item.unit,
            "active": bool(item.active),
        }
    finally:
        session.close()


def list_incomes(user_id: int) -> List[Dict[str, Any]]:
    session = SessionLocal()
    try:
        items_index = _get_items_index(session, user_id)
        incomes = session.execute(select(WarehouseIncome).where(WarehouseIncome.user_id == user_id)).scalars().all()
        result = []
        for income in incomes:
            item_info = items_index.get(income.item)
            unit_val = item_info.unit if item_info else income.unit
            result.append(
                {
                    "id": income.id,
                    "item": income.item,
                    "invoiceNumber": income.invoice_number,
                    "date": format_date(income.date),
                    "qty": income.qty,
                    "unit": unit_val or "",
                    "inStock": bool(income.in_stock),
                }
            )
        return result
    finally:
        session.close()


def _ensure_existing_item(session, user_id: int, item_name: str) -> WarehouseItem:
    item = session.execute(
        select(WarehouseItem).where(WarehouseItem.user_id == user_id, WarehouseItem.name == item_name)
    ).scalar_one_or_none()
    if not item:
        raise ValueError(f'Item "{item_name}" not found in warehouse items.')
    return item


def create_income(payload: Dict[str, Any], user_id: int) -> None:
    item_name = _normalize_item_name(payload.get("item"))
    if not item_name:
        raise ValueError("Item is required")

    session = SessionLocal()
    try:
        item_info = _ensure_existing_item(session, user_id, item_name)
        income = WarehouseIncome(
            user_id=user_id,
            item=item_name,
            invoice_number=normalize_text(payload.get("invoiceNumber")),
            date=parse_date(payload.get("date")),
            qty=to_float(payload.get("qty")) if payload.get("qty") not in (None, "") else 0,
            unit=item_info.unit or "",
            in_stock=_normalize_in_stock(payload.get("inStock")),
        )
        session.add(income)
        commit_with_retry(session)
    finally:
        session.close()


def update_income(payload: Dict[str, Any], user_id: int) -> None:
    income_id = payload.get("id")
    if not income_id:
        raise ValueError("id is required for updateIncome")

    session = SessionLocal()
    try:
        income = session.execute(
            select(WarehouseIncome).where(WarehouseIncome.id == income_id, WarehouseIncome.user_id == user_id)
        ).scalar_one_or_none()
        if not income:
            raise ValueError("Income not found")
        item_name = _normalize_item_name(payload.get("item"))
        if not item_name:
            raise ValueError("Item is required")
        item_info = _ensure_existing_item(session, user_id, item_name)
        income.item = item_name
        income.invoice_number = normalize_text(payload.get("invoiceNumber"))
        income.date = parse_date(payload.get("date"))
        income.qty = to_float(payload.get("qty")) if payload.get("qty") not in (None, "") else 0
        income.unit = item_info.unit or ""
        income.in_stock = _normalize_in_stock(payload.get("inStock"))
        commit_with_retry(session)
    finally:
        session.close()


def delete_income(income_id: str, user_id: int) -> None:
    if not income_id:
        raise ValueError("id is required for deleteIncome")
    session = SessionLocal()
    try:
        income = session.execute(
            select(WarehouseIncome).where(WarehouseIncome.id == income_id, WarehouseIncome.user_id == user_id)
        ).scalar_one_or_none()
        if not income:
            raise ValueError("Income not found")
        session.delete(income)
        commit_with_retry(session)
    finally:
        session.close()


def get_income(income_id: str, user_id: int) -> Dict[str, Any] | None:
    session = SessionLocal()
    try:
        income = session.execute(
            select(WarehouseIncome).where(WarehouseIncome.id == income_id, WarehouseIncome.user_id == user_id)
        ).scalar_one_or_none()
        if not income:
            return None
        return {
            "id": income.id,
            "item": income.item,
            "invoiceNumber": income.invoice_number,
            "date": format_date(income.date),
            "qty": income.qty,
            "unit": income.unit,
            "inStock": bool(income.in_stock),
        }
    finally:
        session.close()


def _extract_contract_items(contract: Contract) -> List[Dict[str, Any]]:
    items = [
        {
            "item": item.item,
            "qty": item.qty,
            "planQty": item.plan_qty,
            "planDate": item.plan_date,
            "dateFact": item.date_fact,
            "delivered": item.delivered,
        }
        for item in contract.items
    ]
    if not items:
        items = [
            {
                "item": contract.item,
                "qty": contract.qty,
                "planQty": contract.plan_qty,
                "planDate": contract.plan_date,
                "dateFact": contract.date_fact,
                "delivered": contract.delivered,
            }
        ]
    return items


def _build_expenses(session, user_id: int) -> List[Dict[str, Any]]:
    contracts = (
        session.execute(select(Contract).where(Contract.user_id == user_id).order_by(Contract.order_index))
        .scalars()
        .all()
    )
    result = []
    for contract in contracts:
        items = _extract_contract_items(contract)
        for item in items:
            item_name = _normalize_item_name(item.get("item") or contract.item)
            date_fact = item.get("dateFact") or contract.date_fact
            if not item_name or not date_fact:
                continue
            if isinstance(date_fact, datetime):
                date_fact = date_fact.date()
            if date_fact < EXPENSES_CUTOFF:
                continue

            qty = item.get("delivered") or item.get("qty") or 0
            result.append(
                {
                    "id": contract.id,
                    "org": contract.org or "",
                    "date": format_date(date_fact),
                    "item": item_name,
                    "qty": qty,
                    "contractNumber": contract.number or "",
                }
            )
    return result


def list_expenses(user_id: int) -> List[Dict[str, Any]]:
    session = SessionLocal()
    try:
        return _build_expenses(session, user_id)
    finally:
        session.close()


def delete_expense(payload: Dict[str, Any], user_id: int) -> None:
    expense_id = payload.get("id")
    if not expense_id:
        raise ValueError("id is required for deleteExpense")

    session = SessionLocal()
    try:
        contract = session.execute(
            select(Contract).where(Contract.id == expense_id, Contract.user_id == user_id)
        ).scalar_one_or_none()
        if not contract:
            raise ValueError("Contract not found for expense %s" % expense_id)
        contract.date_fact = None
        contract.delivered = 0
        commit_with_retry(session)
    finally:
        session.close()


def balances_by_date(date_str: str | None, user_id: int) -> List[Dict[str, Any]]:
    session = SessionLocal()
    try:
        end_date = parse_date(date_str) or date.today()
        end_time = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59, 999000)

        incomes = session.execute(select(WarehouseIncome).where(WarehouseIncome.user_id == user_id)).scalars().all()
        expenses = _build_expenses(session, user_id)
        balances: Dict[str, float] = {}

        for income in incomes:
            if not income.date:
                continue
            income_date = datetime.combine(income.date, datetime.min.time())
            if income_date > end_time:
                continue
            if not _normalize_in_stock(income.in_stock):
                continue
            qty = income.qty or 0
            balances[income.item] = balances.get(income.item, 0) + qty

        for expense in expenses:
            exp_date = parse_date(expense.get("date"))
            if not exp_date:
                continue
            if datetime.combine(exp_date, datetime.min.time()) > end_time:
                continue
            item = _normalize_item_name(expense.get("item"))
            balances[item] = balances.get(item, 0) - (expense.get("qty") or 0)

        items = session.execute(select(WarehouseItem).where(WarehouseItem.user_id == user_id)).scalars().all()
        result = []
        for item in items:
            qty = balances.get(item.name, 0)
            result.append(
                {
                    "date": format_date(end_date),
                    "item": item.name,
                    "qty": qty,
                }
            )

        return sorted(result, key=lambda row: row["item"])
    finally:
        session.close()


def list_moves(user_id: int) -> List[Dict[str, Any]]:
    session = SessionLocal()
    try:
        incomes = session.execute(select(WarehouseIncome).where(WarehouseIncome.user_id == user_id)).scalars().all()
        expenses = _build_expenses(session, user_id)
        moves = []
        for income in incomes:
            moves.append(
                {
                    "date": format_date(income.date),
                    "contractNumber": income.invoice_number or "",
                    "item": income.item,
                    "operationType": "Income",
                    "qty": income.qty,
                }
            )
        for expense in expenses:
            moves.append(
                {
                    "date": expense.get("date", ""),
                    "contractNumber": expense.get("contractNumber", ""),
                    "item": expense.get("item", ""),
                    "operationType": "Expense",
                    "qty": expense.get("qty", 0),
                }
            )

        def sort_key(entry):
            d = parse_date(entry.get("date"))
            return d or date.min

        return sorted(moves, key=sort_key)
    finally:
        session.close()


def handle(action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = _require_user_id(payload)
    if action == "balancesByDate":
        return {"success": True, "data": balances_by_date(payload.get("date"), user_id)}
    if action == "listIncomes":
        return {"success": True, "data": list_incomes(user_id)}
    if action == "listExpenses":
        return {"success": True, "data": list_expenses(user_id)}
    if action == "deleteExpense":
        delete_expense(payload, user_id)
        return {"success": True, "data": list_expenses(user_id)}
    if action == "createIncome":
        create_income(payload, user_id)
        return {"success": True, "data": list_incomes(user_id)}
    if action == "updateIncome":
        update_income(payload, user_id)
        return {"success": True, "data": list_incomes(user_id)}
    if action == "deleteIncome":
        delete_income(payload.get("id"), user_id)
        return {"success": True, "data": list_incomes(user_id)}
    if action == "getIncomeById":
        return {"success": True, "data": get_income(payload.get("id"), user_id)}
    if action == "listMoves":
        return {"success": True, "data": list_moves(user_id)}
    if action == "listItems":
        return {"success": True, "data": list_items(user_id)}
    if action == "createItem":
        create_item(payload, user_id)
        return {"success": True, "data": list_items(user_id)}
    if action == "updateItem":
        update_item(payload, user_id)
        return {"success": True, "data": list_items(user_id)}
    if action == "deleteItem":
        delete_item(payload.get("id"), user_id)
        return {"success": True, "data": list_items(user_id)}
    if action == "getItemById":
        return {"success": True, "data": get_item(payload.get("id"), user_id)}
    raise ValueError(f"Unknown warehouse action: {action}")
