"""挂牌价修正：批次 / 修正项 / 价格版本。

- CorrectionBatch：门店集中提报、研究组审批后统一下发的最小单位；
  状态机见 constants.enums.CorrectionStatus，任一基线冲突整批暂停。
- CorrectionItem：单房源一条（batch_id + listing_id 唯一），
  base_price 是提交时的房源基线，审批与发布两道闸口都拿它核对当前价。
- ListingPriceVersion：每一次单价变化追加一行（修正/手工/采集/撤回），
  只增不删——发布失败回滚后旧版本仍在，撤回也只是再追加一条反向版本。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base
from src.utils.timeutil import utcnow


class CorrectionBatch(Base):
    __tablename__ = "correction_batches"
    __table_args__ = (Index("ix_correction_batches_status", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    batch_no: Mapped[str] = mapped_column(String(32), unique=True)
    # 幂等键：客户端生成，重复提报（双击 / 重试 / 刷新重发）命中唯一索引后直接返回原批次
    request_key: Mapped[str] = mapped_column(String(80), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="submitted")
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    applied_count: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(String(200), default="")
    submitted_by: Mapped[str] = mapped_column(String(160))
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    reviewed_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 冲突证据快照：[{listing_id, base_price, target_price, current_price, reason}]
    conflict_detail: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 发布失败原因（旧价已随事务回滚保留）
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class CorrectionItem(Base):
    __tablename__ = "correction_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "listing_id", name="uq_correction_item_batch_listing"),
        Index("ix_correction_items_listing", "listing_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("correction_batches.id"), index=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    base_price: Mapped[int | None] = mapped_column(Integer, nullable=True)   # 提交时基线（原值）
    target_price: Mapped[int] = mapped_column(Integer)                        # 提交值
    status: Mapped[str] = mapped_column(String(16), default="pending")
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ListingPriceVersion(Base):
    """挂牌单价版本：只追加不修改，审计与撤回都靠它回溯。"""

    __tablename__ = "listing_price_versions"
    __table_args__ = (Index("ix_price_versions_listing", "listing_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    batch_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(16))
    old_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_email: Mapped[str] = mapped_column(String(160), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
