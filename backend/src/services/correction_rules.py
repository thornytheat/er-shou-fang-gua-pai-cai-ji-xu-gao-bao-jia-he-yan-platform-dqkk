"""修正批次的纯规则：状态机守卫、基线核对、撤回计划（不依赖 ORM，可单测）。

口径：
  原值 = 提交时快照的房源基线 base_price
  提交值 = 门店要改成的 target_price
  当前值 = 审批 / 发布 / 撤回那一刻的房源实时单价
任一修正项的 当前值 ≠ 原值 → 整批暂停（conflict），三值全部列出。
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from src.constants.enums import CorrectionStatus as CS
from src.utils.errors import AppError

# 动作 -> {当前状态: 走向}；proceed=允许执行，noop=幂等返回（重复操作不重复生效）
ACTION_GUARDS: dict[str, dict[str, str]] = {
    "approve": {
        CS.SUBMITTED.value: "proceed",
        CS.APPROVED.value: "noop",      # 重复审批：直接返回已审批状态
    },
    "publish": {
        CS.APPROVED.value: "proceed",
        CS.FAILED.value: "proceed",     # 失败后重新发布
        CS.APPLIED.value: "noop",       # 重复发布 / 重试已生效批次：不重复改价
    },
    "retry": {
        CS.FAILED.value: "proceed",
        CS.APPLIED.value: "noop",       # 重试不重复
    },
    "rebase": {
        CS.CONFLICT.value: "proceed",   # 人工确认以当前价为新基线，回到待发布
    },
    "withdraw": {
        CS.SUBMITTED.value: "proceed",
        CS.APPROVED.value: "proceed",
        CS.CONFLICT.value: "proceed",
        CS.FAILED.value: "proceed",
        CS.APPLIED.value: "proceed",
        CS.WITHDRAWN.value: "noop",     # 重复撤回：幂等
    },
}


def guard(status: str, action: str) -> str:
    """校验「状态 × 动作」。返回 proceed / noop；不允许则 409 BATCH_STATE。"""
    table = ACTION_GUARDS.get(action, {})
    outcome = table.get(status)
    if outcome is None:
        raise AppError(
            "BATCH_STATE",
            409,
            detail={"status": status, "action": action},
        )
    return outcome


def find_conflicts(
    items: Iterable[Mapping],
    current_prices: Mapping[int, int | None],
) -> list[dict]:
    """基线核对：逐项比对 当前值 与 原值。

    items: 每条至少含 listing_id / base_price / target_price。
    current_prices: {listing_id: 当前单价}；房源被删时对应键缺失 → 也算冲突。
    返回冲突清单（三值 + 原因），空列表 = 无冲突可原子生效。
    """
    conflicts: list[dict] = []
    for item in items:
        listing_id = int(item["listing_id"])
        base_price = item["base_price"]
        target_price = item["target_price"]
        if listing_id not in current_prices:
            conflicts.append(
                {
                    "listing_id": listing_id,
                    "base_price": base_price,
                    "target_price": target_price,
                    "current_price": None,
                    "reason": "房源已不存在，无法按基线核对",
                }
            )
            continue
        current_price = current_prices[listing_id]
        if current_price != base_price:
            conflicts.append(
                {
                    "listing_id": listing_id,
                    "base_price": base_price,
                    "target_price": target_price,
                    "current_price": current_price,
                    "reason": "等待期间当前价已偏离提交基线（采集 / 手工 / 其它批次改过）",
                }
            )
    return conflicts


def plan_withdraw(
    items: Iterable[Mapping],
    current_prices: Mapping[int, int | None],
) -> dict:
    """已生效批次的撤回计划。

    只有 当前值 == 提交值（即生效后没人再动过）的房源才回退到原值；
    当前值已被后续采集 / 手工改写的房源列入 skip——撤回不能盖掉后续数据。
    """
    restore: list[dict] = []
    skip: list[dict] = []
    for item in items:
        listing_id = int(item["listing_id"])
        base_price = item["base_price"]
        target_price = item["target_price"]
        current_price = current_prices.get(listing_id)
        entry = {
            "listing_id": listing_id,
            "base_price": base_price,
            "target_price": target_price,
            "current_price": current_price,
        }
        if current_price is None:
            skip.append({**entry, "reason": "房源已不存在，跳过回退"})
        elif current_price != target_price:
            skip.append({**entry, "reason": "当前价已被后续采集 / 修正改写，保留新数据不回退"})
        else:
            restore.append(entry)
    return {"restore": restore, "skip": skip}
