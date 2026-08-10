"""Helpers for serializing DeletionRequest rows with user-name enrichment."""
from __future__ import annotations

from typing import Iterable, List, Dict, Any
from sqlalchemy.orm import Session

from PE.weespas.models.deletion_request import DeletionRequest
from PE.weespas.models.user import User
from PE.weespas.schemas.auth import DeletionRequestResponse


def serialize_deletion_requests(
    db: Session,
    requests: Iterable[DeletionRequest],
) -> List[Dict[str, Any]]:
    """Serialize a list of DeletionRequest rows, attaching the names of the
    target / requester / reviewer users in a single batched lookup.
    """
    rows = list(requests)
    if not rows:
        return []

    user_ids: set[str] = set()
    for r in rows:
        if r.target_user_id:
            user_ids.add(r.target_user_id)
        if r.requested_by_id:
            user_ids.add(r.requested_by_id)
        if r.reviewed_by_id:
            user_ids.add(r.reviewed_by_id)

    name_by_id: Dict[str, str] = {}
    if user_ids:
        users = db.query(User.id, User.name).filter(User.id.in_(user_ids)).all()
        name_by_id = {uid: name for uid, name in users}

    out: List[Dict[str, Any]] = []
    for r in rows:
        payload = DeletionRequestResponse.model_validate(r).model_dump()
        live_target_name = name_by_id.get(r.target_user_id) if r.target_user_id else None
        payload["target_user_name"] = (
            live_target_name
            or getattr(r, "target_user_name_snapshot", None)
        )
        payload["requested_by_name"] = (
            name_by_id.get(r.requested_by_id) if r.requested_by_id else None
        )
        payload["reviewed_by_name"] = (
            name_by_id.get(r.reviewed_by_id) if r.reviewed_by_id else None
        )
        out.append(payload)
    return out
