"""挂牌价修正批次：提报 → 审批 → 发布 → （撤回），全链路幂等与原子。

并发与一致性约定：
  1. 每个写操作先 `SELECT ... FOR UPDATE` 锁批次行，状态机守卫见 correction_rules.guard；
     重复审批 / 重复发布 / 重复撤回一律幂等返回，不重复生效。
  2. 审批与发布两道闸口都按「房源基线」核对：当前价 ≠ 提交基线 → 整批转 conflict，
     conflict_detail 列出每项的原值 / 提交值 / 当前值；无冲突才继续。
  3. 发布在同一事务内完成：改价 + 写价格版本 + 重算去重簇 / 均价 / 虚高工单，
     任一环节失败整体回滚——旧版本挂牌价保留，批次转 failed 可重试。
  4. 撤回不删任何数据：未生效批次只改状态；已生效批次按 plan_withdraw 回退，
     当前价已被后续采集改写的房源跳过（不覆盖），全程写价格版本与审计。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.constants.enums import (
    CORRECTION_ITEM_LABELS,
    CORRECTION_STATUS_LABELS,
    CORRECTION_STATUS_ORDER,
    PRICE_CHANGE_SOURCE_LABELS,
    CorrectionItemStatus as IS,
    CorrectionStatus as CS,
    PriceChangeSource,
)
from src.constants.limits import CORRECTION_PAGE_LIMIT
from src.models.correction import CorrectionBatch, CorrectionItem, ListingPriceVersion
from src.models.listing import Listing
from src.models.source import Source
from src.services import cluster_service, correction_rules as rules
from src.utils.errors import AppError
from src.utils.timeutil import iso_utc, utcnow


# ---------------------------------------------------------------- 基础查询

def _next_batch_no(db: Session) -> str:
    stamp = utcnow().strftime("%Y%m%d%H%M%S")
    seq = (db.scalar(select(func.count()).select_from(CorrectionBatch)) or 0) + 1
    return f"FIX-{stamp}-{seq:03d}"


def get_batch_or_404(db: Session, batch_id: int) -> CorrectionBatch:
    batch = db.get(CorrectionBatch, batch_id)
    if batch is None:
        raise AppError("NOT_FOUND", 404, detail=f"batch_id={batch_id}")
    return batch


def lock_batch(db: Session, batch_id: int) -> CorrectionBatch:
    """写操作入口：行锁抢占，并发请求串行化，重复操作看到最新状态后幂等返回。"""
    batch = db.scalar(
        select(CorrectionBatch).where(CorrectionBatch.id == batch_id).with_for_update()
    )
    if batch is None:
        raise AppError("NOT_FOUND", 404, detail=f"batch_id={batch_id}")
    return batch


def _items_of(db: Session, batch_id: int) -> list[CorrectionItem]:
    return db.scalars(
        select(CorrectionItem).where(CorrectionItem.batch_id == batch_id).order_by(CorrectionItem.id)
    ).all()


def _lock_listings(db: Session, listing_ids: list[int]) -> dict[int, Listing]:
    """按 id 升序加行锁（固定顺序防死锁），返回 id -> Listing。"""
    if not listing_ids:
        return {}
    rows = db.scalars(
        select(Listing).where(Listing.id.in_(listing_ids)).order_by(Listing.id).with_for_update()
    ).all()
    return {row.id: row for row in rows}


def _rule_view(item: CorrectionItem) -> dict:
    return {
        "listing_id": item.listing_id,
        "base_price": item.base_price,
        "target_price": item.target_price,
    }


def _apply_price(listing: Listing, new_price: int) -> int | None:
    """改单价并联动总价，返回旧价（口径与 listing_service.fix_price 一致）。"""
    old_price = listing.unit_price
    listing.unit_price = int(new_price)
    if listing.area_sqm:
        listing.total_price_wan = round(new_price * listing.area_sqm / 10000, 2)
    listing.updated_at = utcnow()
    return old_price


def _record_version(
    db: Session,
    *,
    listing_id: int,
    source: str,
    old_price: int | None,
    new_price: int | None,
    actor_email: str,
    batch_id: int | None = None,
) -> None:
    if old_price == new_price:
        return  # 无变化不占版本号
    db.add(
        ListingPriceVersion(
            listing_id=listing_id,
            batch_id=batch_id,
            source=source,
            old_price=old_price,
            new_price=new_price,
            actor_email=actor_email,
        )
    )


def _mark_conflict(
    db: Session, batch: CorrectionBatch, items: list[CorrectionItem], conflicts: list[dict]
) -> CorrectionBatch:
    """任一冲突 → 整批暂停：批次转 conflict，冲突项标记，三值清单落库。"""
    conflicted_ids = {entry["listing_id"] for entry in conflicts}
    batch.status = CS.CONFLICT.value
    batch.conflict_detail = conflicts
    for item in items:
        item.status = (
            IS.CONFLICT.value if item.listing_id in conflicted_ids else IS.PENDING.value
        )
    db.flush()
    return batch


# ---------------------------------------------------------------- 提报

def create_batch(
    db: Session,
    *,
    items: list[dict],
    note: str,
    request_key: str,
    actor_email: str,
) -> tuple[CorrectionBatch, bool]:
    """门店集中提报。request_key 命中唯一索引 → 直接返回原批次（重复提报不重复）。"""
    existing = db.scalar(
        select(CorrectionBatch).where(CorrectionBatch.request_key == request_key)
    )
    if existing is not None:
        return existing, False

    listing_ids = [int(item["listing_id"]) for item in items]
    if len(set(listing_ids)) != len(listing_ids):
        raise AppError("VALIDATION", 422, detail="同一批次内房源重复，请合并后再提报")
    listings = {
        row.id: row
        for row in db.scalars(select(Listing).where(Listing.id.in_(listing_ids))).all()
    }
    missing = [lid for lid in listing_ids if lid not in listings]
    if missing:
        raise AppError("VALIDATION", 422, detail=f"房源不存在：{missing}")

    batch = CorrectionBatch(
        batch_no=_next_batch_no(db),
        request_key=request_key,
        status=CS.SUBMITTED.value,
        item_count=len(items),
        note=note,
        submitted_by=actor_email,
        submitted_at=utcnow(),
    )
    db.add(batch)
    db.flush()
    for item in items:
        listing = listings[int(item["listing_id"])]
        db.add(
            CorrectionItem(
                batch_id=batch.id,
                listing_id=listing.id,
                base_price=listing.unit_price,  # 提交这一刻的基线快照（原值）
                target_price=int(item["target_price"]),
                status=IS.PENDING.value,
            )
        )
    db.flush()
    return batch, True


# ---------------------------------------------------------------- 审批 / 发布 / 重定基线 / 撤回

def approve(db: Session, batch: CorrectionBatch, actor_email: str) -> CorrectionBatch:
    """第一道基线闸口：无冲突 → approved；有冲突 → 整批 conflict。重复审批幂等。"""
    if rules.guard(batch.status, "approve") == "noop":
        return batch
    items = _items_of(db, batch.id)
    current = {lid: row.unit_price for lid, row in _lock_listings(db, [i.listing_id for i in items]).items()}
    conflicts = rules.find_conflicts([_rule_view(item) for item in items], current)
    batch.reviewed_by = actor_email
    batch.reviewed_at = utcnow()
    if conflicts:
        return _mark_conflict(db, batch, items, conflicts)
    batch.status = CS.APPROVED.value
    db.flush()
    return batch


def publish(db: Session, batch: CorrectionBatch, actor_email: str, *, action: str = "publish") -> CorrectionBatch:
    """第二道基线闸口 + 原子生效。

    无冲突：同一事务内改价、写版本、重算去重行情与虚高工单（调用方负责 commit，
    失败整体回滚，旧价保留）。有冲突：整批暂停并列出三值。已生效批次幂等返回。
    """
    if rules.guard(batch.status, action) == "noop":
        return batch
    items = _items_of(db, batch.id)
    listings = _lock_listings(db, [item.listing_id for item in items])
    current = {lid: row.unit_price for lid, row in listings.items()}
    conflicts = rules.find_conflicts([_rule_view(item) for item in items], current)
    if conflicts:
        return _mark_conflict(db, batch, items, conflicts)

    now = utcnow()
    for item in items:
        listing = listings[item.listing_id]
        old_price = _apply_price(listing, item.target_price)
        _record_version(
            db,
            listing_id=listing.id,
            source=PriceChangeSource.CORRECTION.value,
            old_price=old_price,
            new_price=item.target_price,
            actor_email=actor_email,
            batch_id=batch.id,
        )
        item.status = IS.APPLIED.value
        item.applied_at = now
    batch.status = CS.APPLIED.value
    batch.applied_count = len(items)
    batch.published_by = actor_email
    batch.published_at = now
    batch.error = None
    # 价格、去重行情、虚高工单在同一事务里刷新，不会各走一套
    cluster_service.recompute(db)
    db.flush()
    return batch


def mark_failed(db: Session, batch_id: int, error: str) -> CorrectionBatch:
    """发布异常后调用：事务已回滚（旧价保留），只把批次标成 failed 供重试。

    只有 approved / failed 能转 failed——conflict、submitted 等状态由守卫
    在正常流程内迁移，异常路径绝不允许顺手改掉它们。
    """
    batch = db.get(CorrectionBatch, batch_id)
    if batch is not None and batch.status in (CS.APPROVED.value, CS.FAILED.value):
        batch.status = CS.FAILED.value
        batch.error = error[:500]
        db.flush()
    return batch


def rebase(db: Session, batch: CorrectionBatch, actor_email: str) -> CorrectionBatch:
    """冲突批次重定基线：以当前价为新基线重新核对，通过则回到待发布。"""
    rules.guard(batch.status, "rebase")
    items = _items_of(db, batch.id)
    listings = _lock_listings(db, [item.listing_id for item in items])
    for item in items:
        listing = listings.get(item.listing_id)
        if listing is not None:
            item.base_price = listing.unit_price
            item.status = IS.PENDING.value
    current = {lid: row.unit_price for lid, row in listings.items()}
    conflicts = rules.find_conflicts([_rule_view(item) for item in items], current)
    batch.reviewed_by = actor_email
    batch.reviewed_at = utcnow()
    if conflicts:  # 只剩「房源已不存在」一类：仍暂停，等新基线再核对
        return _mark_conflict(db, batch, items, conflicts)
    batch.status = CS.APPROVED.value
    batch.conflict_detail = None
    db.flush()
    return batch


def withdraw(db: Session, batch: CorrectionBatch, actor_email: str) -> CorrectionBatch:
    """撤回：不删数据。已生效批次按撤回计划回退，后续采集改过的房源跳过。"""
    if rules.guard(batch.status, "withdraw") == "noop":
        return batch
    items = _items_of(db, batch.id)
    if batch.status == CS.APPLIED.value:
        listings = _lock_listings(db, [item.listing_id for item in items])
        current = {lid: row.unit_price for lid, row in listings.items()}
        plan = rules.plan_withdraw([_rule_view(item) for item in items], current)
        restore_ids = {entry["listing_id"] for entry in plan["restore"]}
        skip_ids = {entry["listing_id"] for entry in plan["skip"]}
        for entry in plan["restore"]:
            listing = listings[entry["listing_id"]]
            old_price = _apply_price(listing, entry["base_price"])
            _record_version(
                db,
                listing_id=listing.id,
                source=PriceChangeSource.WITHDRAW.value,
                old_price=old_price,
                new_price=entry["base_price"],
                actor_email=actor_email,
                batch_id=batch.id,
            )
        for item in items:
            if item.listing_id in restore_ids:
                item.status = IS.RESTORED.value
            elif item.listing_id in skip_ids:
                item.status = IS.SKIPPED.value
        # 回退后的价格同样原子刷新行情与工单
        cluster_service.recompute(db)
    else:
        for item in items:
            if item.status in (IS.PENDING.value, IS.CONFLICT.value):
                item.status = IS.SKIPPED.value
    batch.status = CS.WITHDRAWN.value
    db.flush()
    return batch


# ---------------------------------------------------------------- 查询与序列化

def counts(db: Session) -> dict:
    rows = db.execute(
        select(CorrectionBatch.status, func.count()).group_by(CorrectionBatch.status)
    ).all()
    by_status = {status: int(total) for status, total in rows}
    return {
        "total": sum(by_status.values()),
        "by_status": {key: by_status.get(key, 0) for key in CORRECTION_STATUS_ORDER},
    }


def list_batches(db: Session, *, status: str | None = None, limit: int = CORRECTION_PAGE_LIMIT) -> dict:
    query = select(CorrectionBatch)
    if status:
        query = query.where(CorrectionBatch.status == status)
    rows = db.scalars(query.order_by(CorrectionBatch.id.desc()).limit(limit)).all()
    return {
        "items": [serialize_batch(db, row, with_items=False) for row in rows],
        "counts": counts(db),
    }


def serialize_batch(db: Session, batch: CorrectionBatch, *, with_items: bool = True) -> dict:
    payload = {
        "id": batch.id,
        "batch_no": batch.batch_no,
        "request_key": batch.request_key,
        "status": batch.status,
        "status_label": CORRECTION_STATUS_LABELS.get(batch.status, batch.status),
        "item_count": batch.item_count,
        "applied_count": batch.applied_count,
        "note": batch.note,
        "submitted_by": batch.submitted_by,
        "submitted_at": iso_utc(batch.submitted_at),
        "reviewed_by": batch.reviewed_by,
        "reviewed_at": iso_utc(batch.reviewed_at),
        "published_by": batch.published_by,
        "published_at": iso_utc(batch.published_at),
        "conflict_detail": batch.conflict_detail or [],
        "error": batch.error,
        "created_at": iso_utc(batch.created_at),
    }
    if not with_items:
        return payload

    items = _items_of(db, batch.id)
    listings = {
        row.id: row
        for row in db.scalars(
            select(Listing).where(Listing.id.in_([item.listing_id for item in items]))
        ).all()
    } if items else {}
    sources = {row.id: row for row in db.scalars(select(Source)).all()}
    payload["items"] = [
        _serialize_item(item, listings.get(item.listing_id), sources) for item in items
    ]
    versions = db.scalars(
        select(ListingPriceVersion)
        .where(ListingPriceVersion.batch_id == batch.id)
        .order_by(ListingPriceVersion.id)
    ).all()
    payload["versions"] = [
        {
            "id": version.id,
            "listing_id": version.listing_id,
            "source": version.source,
            "source_label": PRICE_CHANGE_SOURCE_LABELS.get(version.source, version.source),
            "old_price": version.old_price,
            "new_price": version.new_price,
            "actor_email": version.actor_email,
            "created_at": iso_utc(version.created_at),
        }
        for version in versions
    ]
    return payload


def _serialize_item(
    item: CorrectionItem, listing: Listing | None, sources: dict[int, Source]
) -> dict:
    source = sources.get(listing.source_id) if listing else None
    return {
        "id": item.id,
        "listing_id": item.listing_id,
        "community": listing.community if listing else None,
        "layout": listing.layout if listing else None,
        "title": listing.title if listing else None,
        "source_name": source.name if source else None,
        "base_price": item.base_price,
        "target_price": item.target_price,
        # 当前值实时取，审批 / 发布 / 撤回看到的三值永远是最新口径
        "current_price": listing.unit_price if listing else None,
        "status": item.status,
        "status_label": CORRECTION_ITEM_LABELS.get(item.status, item.status),
        "applied_at": iso_utc(item.applied_at),
    }
