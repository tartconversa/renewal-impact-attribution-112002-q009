"""输入加载与修订解析。

设计约定（见 docs/input-contract.md）：

- 空单元格表示"未知/未提供"，统一解析为 ``None``，绝不解释为数值零；
  缺失是否可参与计算由下游样本逻辑决定。
- 指标可能延迟上报或事后修订：``area-periods`` 同一 (片区, 期别) 若出现
  多份记录，以 ``received_at`` 最新的一份覆盖旧版（旧版仍保留在原始数据中，
  配合 ``as_of`` 快照可复现旧分析）。
- 干预记录的 ``revision_of`` 指向被取代的旧记录；修订记录同样解析实际开放日期，
  以"实际开放时间可能与计划不同"。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

# area-periods.csv 中属于指标数值的列（residents 也是其中之一，但作为人口上下文处理）
METRIC_COLUMNS = ("residents", "service_use", "walkability_score")


def _read_csv(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def parse_int(value: str | None) -> int | None:
    """空单元格 -> None；其余按整数解析。"""
    if value is None or not value.strip():
        return None
    return int(value.strip())


def parse_date(value: str | None) -> date | None:
    """ISO 8601 日期；空单元格 -> None（日期未知，不是某个默认日期）。"""
    if value is None or not value.strip():
        return None
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()


@dataclass
class PeriodObs:
    area_id: str
    period: str
    values: dict[str, int | None]
    received_at: date
    source_row: int | None = None


@dataclass
class Intervention:
    intervention_id: str
    area_id: str
    type: str
    planned_start: date | None
    actual_open: date | None
    revision_of: str | None
    superseded: bool = False


@dataclass
class OtherMeasure:
    measure_id: str
    area_id: str
    type: str
    start_period: str
    end_period: str | None


@dataclass
class AreaHistoryRow:
    boundary_version: str
    area_id: str
    block: str
    residents: int
    valid_from: date


def load_period_rows(path: str | Path) -> list[PeriodObs]:
    obs: list[PeriodObs] = []
    for index, row in enumerate(_read_csv(path), start=2):
        received = parse_date(row["received_at"])
        if received is None:
            raise ValueError(f"{path} 第 {index} 行缺少 received_at，无法确定数据版本")
        obs.append(
            PeriodObs(
                area_id=row["area_id"].strip(),
                period=row["period"].strip(),
                values={col: parse_int(row.get(col)) for col in METRIC_COLUMNS},
                received_at=received,
                source_row=index,
            )
        )
    return obs


def as_of_snapshot(rows: list[PeriodObs], as_of: date | None) -> list[PeriodObs]:
    """截至 ``as_of``（含当天）的数据快照。

    迟到上报在 ``as_of`` 之后到达的记录被排除；同一 (片区, 期别) 取
    快照内 ``received_at`` 最新的一份，从而旧分析可被精确复现。
    """
    visible = [row for row in rows if as_of is None or row.received_at <= as_of]
    latest: dict[tuple[str, str], PeriodObs] = {}
    for row in visible:
        key = (row.area_id, row.period)
        old = latest.get(key)
        if old is None or row.received_at >= old.received_at:
            latest[key] = row
    return sorted(latest.values(), key=lambda r: (r.area_id, r.period))


def load_interventions(path: str | Path) -> list[Intervention]:
    """加载干预记录并标记被修订取代的旧记录。

    若记录 A 的 ``revision_of`` 指向记录 B，则 B 标记为 superseded。
    有效干预以修订后的记录为准；旧记录保留以便审计与版本对比。
    """
    records = [
        Intervention(
            intervention_id=row["intervention_id"].strip(),
            area_id=row["area_id"].strip(),
            type=row["type"].strip(),
            planned_start=parse_date(row.get("planned_start")),
            actual_open=parse_date(row.get("actual_open")),
            revision_of=(row.get("revision_of") or "").strip() or None,
        )
        for row in _read_csv(path)
    ]
    superseded_ids = {r.revision_of for r in records if r.revision_of}
    for record in records:
        if record.intervention_id in superseded_ids:
            record.superseded = True
    return records


def load_other_measures(path: str | Path) -> list[OtherMeasure]:
    measures = []
    for row in _read_csv(path):
        start = (row.get("start_period") or "").strip()
        if not start:
            raise ValueError(f"{path} 中 {row.get('measure_id')} 缺少 start_period")
        end = (row.get("end_period") or "").strip()
        measures.append(
            OtherMeasure(
                measure_id=row["measure_id"].strip(),
                area_id=row["area_id"].strip(),
                type=row["type"].strip(),
                start_period=start,
                end_period=end or None,
            )
        )
    return measures


def load_area_history(path: str | Path) -> list[AreaHistoryRow]:
    history = []
    for row in _read_csv(path):
        valid_from = parse_date(row.get("valid_from"))
        if valid_from is None:
            raise ValueError(f"{path} 中 {row.get('area_id')}/{row.get('block')} 缺少 valid_from")
        residents = parse_int(row.get("residents"))
        if residents is None:
            raise ValueError(f"{path} 中 {row.get('area_id')}/{row.get('block')} 缺少 residents")
        history.append(
            AreaHistoryRow(
                boundary_version=row["boundary_version"].strip(),
                area_id=row["area_id"].strip(),
                block=row["block"].strip(),
                residents=residents,
                valid_from=valid_from,
            )
        )
    return history
