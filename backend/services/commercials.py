from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import select, func

from backend.db import SessionLocal, commit_with_retry
from backend.models import CommercialsState
from backend.services.utils import json_dump, json_load, normalize_text


def _require_user_id(payload: Dict[str, Any]) -> int:
    raw = payload.get("userId")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError("userId is required")

TEMPLATE_OPTIONS = [
    "КП Авангард",
    "КП Грин",
    "КП Интеко",
    "КП Столяров М.А.",
    "КП Столярова",
    "КП фарма",
    "КП Выбор",
]

DEFAULT_HEADERS = ["№", "Наименование", "Ед.изм", "Кол-во", "Цена"]


def _ensure_state(session, user_id: int) -> CommercialsState:
    state = session.execute(select(CommercialsState).where(CommercialsState.user_id == user_id)).scalar_one_or_none()
    if not state:
        next_id = session.execute(select(func.max(CommercialsState.id))).scalar()
        state = CommercialsState(
            id=(next_id or 0) + 1,
            user_id=user_id,
            headers=json_dump(DEFAULT_HEADERS),
            main_rows=json_dump([]),
            params=json_dump({"j3": "", "j4": "", "j5": "", "j6": ""}),
            templates=json_dump({"kp1": "", "kp2": "", "kp3": ""}),
            kp_tables=json_dump([]),
            organization="",
            organization_options=json_dump([]),
        )
        session.add(state)
        commit_with_retry(session)
    return state


def _load_state_data(state: CommercialsState) -> Dict[str, Any]:
    headers = json_load(state.headers, DEFAULT_HEADERS)
    main_rows = json_load(state.main_rows, [])
    params = json_load(state.params, {"j3": "", "j4": "", "j5": "", "j6": ""})
    templates = json_load(state.templates, {"kp1": "", "kp2": "", "kp3": ""})
    kp_tables = json_load(state.kp_tables, [])
    org_options = json_load(state.organization_options, [])

    if not main_rows:
        main_rows = [
            {"row": idx + 3, "values": ["", "", "", "", ""]}
            for idx in range(84)
        ]

    return {
        "headers": headers,
        "mainRows": main_rows,
        "params": params,
        "templates": templates,
        "templateOptions": TEMPLATE_OPTIONS,
        "kpTables": kp_tables,
        "organization": state.organization or "",
        "organizationOptions": org_options,
        "folderUrl": "/commercials/files",
        "sheetUrl": "/commercials",
        "downloadHint": "Файлы сохраняются в локальную папку выгрузок.",
    }


def load_data(user_id: int) -> Dict[str, Any]:
    session = SessionLocal()
    try:
        state = _ensure_state(session, user_id)
        return _load_state_data(state)
    finally:
        session.close()


def save_main(rows: List[Dict[str, Any]], rebuild: bool, user_id: int) -> Dict[str, Any]:
    session = SessionLocal()
    try:
        state = _ensure_state(session, user_id)
        normalized = []
        for idx in range(84):
            row = rows[idx] if idx < len(rows) else None
            values = row.get("values") if row and isinstance(row.get("values"), list) else []
            normalized.append({"row": idx + 3, "values": (values + ["", "", "", "", ""])[:5]})
        state.main_rows = json_dump(normalized)
        commit_with_retry(session)
        return _load_state_data(state)
    finally:
        session.close()


def save_params(
    params: Dict[str, Any], templates: Dict[str, Any], organization: str, rebuild: bool, user_id: int
) -> Dict[str, Any]:
    session = SessionLocal()
    try:
        state = _ensure_state(session, user_id)
        current_params = json_load(state.params, {})
        current_params.update({
            "j3": params.get("j3", ""),
            "j4": params.get("j4", ""),
            "j5": params.get("j5", ""),
            "j6": params.get("j6", ""),
        })
        state.params = json_dump(current_params)

        current_templates = json_load(state.templates, {})
        current_templates.update({
            "kp1": templates.get("kp1", ""),
            "kp2": templates.get("kp2", ""),
            "kp3": templates.get("kp3", ""),
        })
        state.templates = json_dump(current_templates)

        if organization is not None:
            state.organization = normalize_text(organization)
            options = json_load(state.organization_options, [])
            if state.organization and state.organization not in options:
                options.append(state.organization)
            state.organization_options = json_dump(options)

        commit_with_retry(session)
        return _load_state_data(state)
    finally:
        session.close()


def save_kp_tables(tables: List[Dict[str, Any]], user_id: int) -> Dict[str, Any]:
    session = SessionLocal()
    try:
        state = _ensure_state(session, user_id)
        state.kp_tables = json_dump(tables or [])
        commit_with_retry(session)
        return _load_state_data(state)
    finally:
        session.close()


def clear_main(user_id: int) -> Dict[str, Any]:
    return save_main([], rebuild=True, user_id=user_id)


def create_pdfs() -> Dict[str, Any]:
    return {
        "folderUrl": "/commercials/files",
        "downloadUrl": "",
        "zipName": "",
    }


def handle(action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = _require_user_id(payload)
    if action == "load":
        return {"success": True, "data": load_data(user_id)}
    if action == "saveMain":
        return {
            "success": True,
            "data": save_main(payload.get("mainRows") or [], payload.get("rebuild") is not False, user_id),
        }
    if action == "saveParams":
        return {
            "success": True,
            "data": save_params(
                payload.get("params") or {},
                payload.get("templates") or {},
                payload.get("organization") or "",
                payload.get("rebuild") is not False,
                user_id,
            ),
        }
    if action == "saveKpTables":
        return {"success": True, "data": save_kp_tables(payload.get("tables") or [], user_id)}
    if action == "clearMain":
        return {"success": True, "data": clear_main(user_id)}
    if action == "rebuildKp":
        return {"success": True, "data": load_data(user_id)}
    if action == "createPdfs":
        return {"success": True, "data": create_pdfs()}
    raise ValueError(f"Unknown commercials action: {action}")
