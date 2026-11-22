"""片区边界版本、人口构成与范围断裂。

数据来自 ``area-history.csv``（边界版本 × 片区 × 区块 × 人口 × 生效日期）。

核心区分：

1. **范围断裂（scope break）**：前后两期适用的边界版本不同，片区覆盖的
   区块集合发生增减，此时片区总量的前后差不能直接参与趋势计算，
   只能按区块分解后解释。
2. **总量变化 vs 同类居民变化**：在共同覆盖的"持续区块"上计算的变化，
   才反映同一地理范围上的居民变化；新增/流失区块的人口单独列出。
3. 指标若为计数（如 service_use），还可做数量/强度分解（见 estimators），
   以区分"人多了所以用量多"与"同等居民用量提高"。
"""
from __future__ import annotations

from dataclasses import dataclass

from .loading import AreaHistoryRow
from .periods import period_bounds


def _active_rows(history: list[AreaHistoryRow], area_id: str, period: str) -> list[AreaHistoryRow]:
    """期别起始时点生效的边界版本对应的全部区块行。

    版本以 ``valid_from`` 不晚于该期别期初的最新一条为准；若该版本在
    期初之后、期内再次修订，则期内仍按期初版本统计，下期起才切换，
    保证一个期别对应唯一、稳定的口径。
    """
    period_start = period_bounds(period)[0]
    applicable = [
        row for row in history
        if row.area_id == area_id and row.valid_from <= period_start
    ]
    if not applicable:
        return []
    current_version = max(applicable, key=lambda row: row.valid_from).boundary_version
    version_rows = [row for row in applicable if row.boundary_version == current_version]
    # 同区块若有多条历史行，取 valid_from 最新的一条
    latest_per_block: dict[str, AreaHistoryRow] = {}
    for row in version_rows:
        old = latest_per_block.get(row.block)
        if old is None or row.valid_from >= old.valid_from:
            latest_per_block[row.block] = row
    return list(latest_per_block.values())


def version_at(history: list[AreaHistoryRow], area_id: str, period: str) -> str | None:
    """该期别起始时点适用的边界版本。"""
    rows = _active_rows(history, area_id, period)
    return rows[0].boundary_version if rows else None


def blocks_at(history: list[AreaHistoryRow], area_id: str, period: str) -> dict[str, int]:
    """该期别适用版本下 {区块: 人口}。"""
    return {row.block: row.residents for row in _active_rows(history, area_id, period)}


@dataclass
class PopulationDecomposition:
    area_id: str
    pre_period: str
    post_period: str
    pre_version: str | None
    post_version: str | None
    scope_break: bool
    pre_total: int
    post_total: int
    total_change: int
    persistent_pre: int           # 持续区块（两期共有）的前期人口
    persistent_post: int          # 持续区块的后期人口
    persistent_change: int        # 同类范围内的人口变化
    added_blocks: dict[str, int]  # 后期新增区块
    lost_blocks: dict[str, int]   # 前期流失区块
    note: str


def decompose_population(
    history: list[AreaHistoryRow], area_id: str, pre_period: str, post_period: str
) -> PopulationDecomposition:
    pre = blocks_at(history, area_id, pre_period)
    post = blocks_at(history, area_id, post_period)
    pre_version = version_at(history, area_id, pre_period)
    post_version = version_at(history, area_id, post_period)

    common = sorted(set(pre) & set(post))
    added = {block: post[block] for block in sorted(set(post) - set(pre))}
    lost = {block: pre[block] for block in sorted(set(pre) - set(post))}
    persistent_pre = sum(pre[block] for block in common)
    persistent_post = sum(post[block] for block in common)

    scope_break = pre_version != post_version or bool(added or lost)
    if scope_break:
        note = (
            f"边界版本由 {pre_version or '未知'} 变为 {post_version or '未知'}，"
            "片区范围发生变化：总量前后差含新增/流失区块，不能直接作趋势，"
            "请以持续区块变化为准。"
        )
    else:
        note = "前后两期边界一致，总量变化与持续区块变化口径相同。"

    pre_total, post_total = sum(pre.values()), sum(post.values())
    return PopulationDecomposition(
        area_id=area_id,
        pre_period=pre_period,
        post_period=post_period,
        pre_version=pre_version,
        post_version=post_version,
        scope_break=scope_break,
        pre_total=pre_total,
        post_total=post_total,
        total_change=post_total - pre_total,
        persistent_pre=persistent_pre,
        persistent_post=persistent_post,
        persistent_change=persistent_post - persistent_pre,
        added_blocks=added,
        lost_blocks=lost,
        note=note,
    )


def reconcile_totals(
    history: list[AreaHistoryRow], area_population: dict[str, int | None], area_id: str, period: str
) -> dict[str, object]:
    """对比 area-periods 的人口口径与区块汇总口径，返回核对信息（仅提示，不阻断分析）。"""
    block_total = sum(blocks_at(history, area_id, period).values())
    reported = area_population.get(area_id)
    return {
        "area_id": area_id,
        "period": period,
        "reported_residents": reported,
        "block_sum": block_total or None,
        "gap": (reported - block_total) if reported is not None and block_total else None,
    }
