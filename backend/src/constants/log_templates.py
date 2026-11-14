"""审计动作中文标签。"""
from __future__ import annotations

ACTIONS: dict[str, str] = {
    "auth.login": "登录",
    "http.mutation": "写请求",
    "ingest.run": "运行采集",
    "ingest.schedule": "定时采集",
    "source.update": "修改采集源",
    "source.create": "新建采集源",
    "cluster.recompute": "重算去重簇",
    "flag.detect": "重跑价格核验",
    "flag.review": "复核价格工单",
    "listing.price_fix": "修正挂牌单价",
    "correction.submit": "提报挂牌价修正批次",
    "correction.approve": "审批修正批次",
    "correction.publish": "发布修正批次",
    "correction.retry": "重试发布修正批次",
    "correction.rebase": "重定批次基线",
    "correction.withdraw": "撤回修正批次",
    "export.market": "导出行情 CSV",
}
