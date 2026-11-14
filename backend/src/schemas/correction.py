from __future__ import annotations

from pydantic import BaseModel, Field

from src.constants.limits import MAX_CORRECTION_ITEMS


class CorrectionItemIn(BaseModel):
    listing_id: int = Field(ge=1)
    target_price: int = Field(ge=1000, le=300000)


class CorrectionBatchIn(BaseModel):
    """门店集中提报。request_key 是幂等键：重复提报返回原批次，不重复建单。"""

    request_key: str = Field(min_length=8, max_length=80)
    note: str = Field(min_length=2, max_length=200)
    items: list[CorrectionItemIn] = Field(min_length=1, max_length=MAX_CORRECTION_ITEMS)


class CorrectionItemOut(BaseModel):
    id: int
    listing_id: int
    community: str | None = None
    layout: str | None = None
    title: str | None = None
    source_name: str | None = None
    base_price: int | None = None
    target_price: int
    current_price: int | None = None
    status: str
    status_label: str
    applied_at: str | None = None


class CorrectionVersionOut(BaseModel):
    id: int
    listing_id: int
    source: str
    source_label: str
    old_price: int | None = None
    new_price: int | None = None
    actor_email: str
    created_at: str | None = None


class CorrectionBatchOut(BaseModel):
    id: int
    batch_no: str
    request_key: str
    status: str
    status_label: str
    item_count: int
    applied_count: int
    note: str
    submitted_by: str
    submitted_at: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    published_by: str | None = None
    published_at: str | None = None
    conflict_detail: list[dict] = []
    error: str | None = None
    created_at: str | None = None
    items: list[CorrectionItemOut] = []
    versions: list[CorrectionVersionOut] = []


class CorrectionBatchListOut(BaseModel):
    items: list[CorrectionBatchOut]
    counts: dict
