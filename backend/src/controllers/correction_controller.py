"""挂牌价修正批次控制器：审计 + 事务边界。

事务约定：
  - 审批 / 发布 / 撤回在「同一事务」内完成状态迁移与副作用（改价、版本、重算），
    controller 只在全部成功后 commit 一次——不会出现价格改了但行情没刷新的半态。
  - 状态机守卫抛出的 AppError（如冲突批次禁止发布）直接向上抛 409，不算发布失败；
    只有执行期的意外异常才走「回滚保留旧价 → 批次标 failed → 可重试」。
"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from src.services import audit_service, correction_service
from src.utils.errors import AppError


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def list_batches(db: Session, *, status: str | None = None) -> dict:
    return correction_service.list_batches(db, status=status)


def detail(db: Session, batch_id: int) -> dict:
    batch = correction_service.get_batch_or_404(db, batch_id)
    return correction_service.serialize_batch(db, batch)


def submit(db: Session, payload, principal: dict, request: Request) -> dict:
    items = [item.model_dump() for item in payload.items]
    batch, created = correction_service.create_batch(
        db,
        items=items,
        note=payload.note,
        request_key=payload.request_key,
        actor_email=principal["email"],
    )
    if created:
        audit_service.record(
            db,
            actor_email=principal["email"],
            actor_role=principal["role"],
            action="correction.submit",
            entity="correction_batch",
            entity_id=batch.id,
            detail={"batch_no": batch.batch_no, "item_count": batch.item_count, "note": payload.note},
            ip=_ip(request),
        )
        db.commit()
    # 重复提报（request_key 命中）：不重复建单、不重复写审计，直接返回原批次
    return correction_service.serialize_batch(db, batch)


def approve(db: Session, batch_id: int, principal: dict, request: Request) -> dict:
    batch = correction_service.lock_batch(db, batch_id)
    before = batch.status
    batch = correction_service.approve(db, batch, principal["email"])
    if batch.status != before:  # 幂等命中（重复审批）不重复写审计
        audit_service.record(
            db,
            actor_email=principal["email"],
            actor_role=principal["role"],
            action="correction.approve",
            entity="correction_batch",
            entity_id=batch.id,
            detail={
                "batch_no": batch.batch_no,
                "outcome": batch.status,
                "conflicts": len(batch.conflict_detail or []),
            },
            ip=_ip(request),
        )
        db.commit()
    return correction_service.serialize_batch(db, batch)


def publish(db: Session, batch_id: int, principal: dict, request: Request, *, action: str = "publish") -> dict:
    batch = correction_service.lock_batch(db, batch_id)
    before = batch.status
    try:
        batch = correction_service.publish(db, batch, principal["email"], action=action)
        if batch.status != before:
            audit_service.record(
                db,
                actor_email=principal["email"],
                actor_role=principal["role"],
                action=f"correction.{action}",
                entity="correction_batch",
                entity_id=batch.id,
                detail={
                    "batch_no": batch.batch_no,
                    "outcome": batch.status,
                    "applied": batch.applied_count,
                    "conflicts": len(batch.conflict_detail or []),
                },
                ip=_ip(request),
            )
        db.commit()  # 改价 + 版本 + 重算 + 状态 + 审计，同一事务原子生效
    except AppError:
        # 业务校验（状态守卫等）：回滚本次事务后原样抛出，批次状态不被污染
        db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001 - 发布失败是业务结果：旧价保留，批次可重试
        db.rollback()
        batch = correction_service.mark_failed(db, batch_id, f"{type(exc).__name__}: {exc}")
        audit_service.record(
            db,
            actor_email=principal["email"],
            actor_role=principal["role"],
            action=f"correction.{action}",
            entity="correction_batch",
            entity_id=batch_id,
            detail={"batch_no": batch.batch_no, "outcome": "failed", "error": batch.error},
            ip=_ip(request),
        )
        db.commit()
    return correction_service.serialize_batch(db, correction_service.get_batch_or_404(db, batch_id))


def rebase(db: Session, batch_id: int, principal: dict, request: Request) -> dict:
    batch = correction_service.lock_batch(db, batch_id)
    batch = correction_service.rebase(db, batch, principal["email"])
    audit_service.record(
        db,
        actor_email=principal["email"],
        actor_role=principal["role"],
        action="correction.rebase",
        entity="correction_batch",
        entity_id=batch.id,
        detail={"batch_no": batch.batch_no, "outcome": batch.status},
        ip=_ip(request),
    )
    db.commit()
    return correction_service.serialize_batch(db, batch)


def withdraw(db: Session, batch_id: int, principal: dict, request: Request) -> dict:
    batch = correction_service.lock_batch(db, batch_id)
    before = batch.status
    batch = correction_service.withdraw(db, batch, principal["email"])
    if batch.status != before:
        audit_service.record(
            db,
            actor_email=principal["email"],
            actor_role=principal["role"],
            action="correction.withdraw",
            entity="correction_batch",
            entity_id=batch.id,
            detail={"batch_no": batch.batch_no, "from": before, "applied_count": batch.applied_count},
            ip=_ip(request),
        )
        db.commit()
    return correction_service.serialize_batch(db, batch)
