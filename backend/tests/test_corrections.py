"""修正批次纯规则测试：状态机守卫 / 基线三值核对 / 撤回计划。

被测模块 src/services/correction_rules.py 不依赖 ORM，
输入全是 dict/list，期望值按业务口径手推。
"""
from __future__ import annotations

import pytest

from src.services.correction_rules import find_conflicts, guard, plan_withdraw
from src.utils.errors import AppError

ITEMS = [
    {"listing_id": 11, "base_price": 65686, "target_price": 63900},
    {"listing_id": 12, "base_price": 58100, "target_price": 57500},
]


# ---------------------------------------------------------------- 状态机守卫

@pytest.mark.parametrize(
    ("status", "action", "expected"),
    [
        ("submitted", "approve", "proceed"),
        ("approved", "approve", "noop"),       # 重复审批幂等
        ("approved", "publish", "proceed"),
        ("failed", "publish", "proceed"),      # 失败后可直接重新发布
        ("applied", "publish", "noop"),        # 重复发布不重复改价
        ("failed", "retry", "proceed"),
        ("applied", "retry", "noop"),          # 重试不重复
        ("conflict", "rebase", "proceed"),
        ("submitted", "withdraw", "proceed"),
        ("approved", "withdraw", "proceed"),
        ("conflict", "withdraw", "proceed"),
        ("failed", "withdraw", "proceed"),
        ("applied", "withdraw", "proceed"),
        ("withdrawn", "withdraw", "noop"),     # 重复撤回幂等
    ],
)
def test_guard_allows(status, action, expected):
    assert guard(status, action) == expected


@pytest.mark.parametrize(
    ("status", "action"),
    [
        ("submitted", "publish"),   # 未审批不能发布
        ("submitted", "retry"),
        ("submitted", "rebase"),
        ("approved", "retry"),
        ("approved", "rebase"),
        ("applied", "approve"),
        ("applied", "rebase"),
        ("conflict", "approve"),
        ("conflict", "publish"),    # 冲突批次必须先 rebase 或 withdraw
        ("conflict", "retry"),
        ("failed", "approve"),
        ("failed", "rebase"),
        ("withdrawn", "approve"),
        ("withdrawn", "publish"),
        ("withdrawn", "retry"),
        ("withdrawn", "rebase"),
    ],
)
def test_guard_rejects(status, action):
    with pytest.raises(AppError) as excinfo:
        guard(status, action)
    assert excinfo.value.code == "BATCH_STATE"
    assert excinfo.value.status_code == 409


# ---------------------------------------------------------------- 基线核对（三值）

def test_find_conflicts_clean_when_current_equals_base():
    current = {11: 65686, 12: 58100}
    assert find_conflicts(ITEMS, current) == []


def test_find_conflicts_lists_three_values_when_price_moved():
    current = {11: 64000, 12: 58100}  # 11 在等待期间被采集改价
    conflicts = find_conflicts(ITEMS, current)
    assert len(conflicts) == 1
    entry = conflicts[0]
    assert entry["listing_id"] == 11
    assert entry["base_price"] == 65686      # 原值
    assert entry["target_price"] == 63900    # 提交值
    assert entry["current_price"] == 64000   # 当前值
    assert "偏离" in entry["reason"]


def test_find_conflicts_flags_missing_listing():
    current = {12: 58100}  # 11 已被删除
    conflicts = find_conflicts(ITEMS, current)
    assert len(conflicts) == 1
    assert conflicts[0]["listing_id"] == 11
    assert conflicts[0]["current_price"] is None
    assert "不存在" in conflicts[0]["reason"]


def test_find_conflicts_base_none_matches_current_none():
    # 提交时单价缺失（None），当前仍缺失 → 不算冲突
    items = [{"listing_id": 13, "base_price": None, "target_price": 50000}]
    assert find_conflicts(items, {13: None}) == []
    # 当前有了价 → 冲突
    conflicts = find_conflicts(items, {13: 51000})
    assert len(conflicts) == 1


# ---------------------------------------------------------------- 撤回计划

def test_plan_withdraw_restores_untouched_and_skips_rewritten():
    items = [
        {"listing_id": 11, "base_price": 65686, "target_price": 63900},
        {"listing_id": 12, "base_price": 58100, "target_price": 57500},
        {"listing_id": 13, "base_price": 40000, "target_price": 41000},
    ]
    current = {
        11: 63900,   # 生效后没人动过 → 回退到 65686
        12: 57000,   # 后续采集又改了价 → 跳过，不覆盖新数据
        # 13 房源已删除 → 跳过
    }
    plan = plan_withdraw(items, current)
    assert [entry["listing_id"] for entry in plan["restore"]] == [11]
    assert plan["restore"][0]["base_price"] == 65686
    skipped = {entry["listing_id"]: entry["reason"] for entry in plan["skip"]}
    assert set(skipped) == {12, 13}
    assert "改写" in skipped[12]
    assert "不存在" in skipped[13]
