from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from src.config.database import get_db
from src.constants.roles import (
    CORRECTION_APPROVE_ROLES,
    CORRECTION_READ_ROLES,
    CORRECTION_SUBMIT_ROLES,
)
from src.controllers import correction_controller
from src.middlewares.rbac import require_roles
from src.schemas.correction import CorrectionBatchIn, CorrectionBatchListOut, CorrectionBatchOut

router = APIRouter(prefix="/corrections", tags=["corrections"])


@router.get("", response_model=CorrectionBatchListOut)
def list_batches(
    status: str | None = Query(default=None),
    principal: dict = Depends(require_roles(*CORRECTION_READ_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    return correction_controller.list_batches(db, status=status)


@router.post("", response_model=CorrectionBatchOut)
def submit(
    payload: CorrectionBatchIn,
    request: Request,
    principal: dict = Depends(require_roles(*CORRECTION_SUBMIT_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    return correction_controller.submit(db, payload, principal, request)


@router.get("/{batch_id}", response_model=CorrectionBatchOut)
def detail(
    batch_id: int,
    principal: dict = Depends(require_roles(*CORRECTION_READ_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    return correction_controller.detail(db, batch_id)


@router.post("/{batch_id}/approve", response_model=CorrectionBatchOut)
def approve(
    batch_id: int,
    request: Request,
    principal: dict = Depends(require_roles(*CORRECTION_APPROVE_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    return correction_controller.approve(db, batch_id, principal, request)


@router.post("/{batch_id}/publish", response_model=CorrectionBatchOut)
def publish(
    batch_id: int,
    request: Request,
    principal: dict = Depends(require_roles(*CORRECTION_APPROVE_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    return correction_controller.publish(db, batch_id, principal, request)


@router.post("/{batch_id}/retry", response_model=CorrectionBatchOut)
def retry(
    batch_id: int,
    request: Request,
    principal: dict = Depends(require_roles(*CORRECTION_APPROVE_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    return correction_controller.publish(db, batch_id, principal, request, action="retry")


@router.post("/{batch_id}/rebase", response_model=CorrectionBatchOut)
def rebase(
    batch_id: int,
    request: Request,
    principal: dict = Depends(require_roles(*CORRECTION_APPROVE_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    return correction_controller.rebase(db, batch_id, principal, request)


@router.post("/{batch_id}/withdraw", response_model=CorrectionBatchOut)
def withdraw(
    batch_id: int,
    request: Request,
    principal: dict = Depends(require_roles(*CORRECTION_APPROVE_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    return correction_controller.withdraw(db, batch_id, principal, request)
