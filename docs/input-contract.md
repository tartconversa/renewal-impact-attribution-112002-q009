# 合成输入材料

这里的记录均为合成数据，不对应真实住户、项目或城市。每个文件表示一个来源数据表，不包含分析结论；解释记录时应保留来源标识和修订关系。

- `area-periods.csv`：CSV 表格，字段为 `area_id, period, residents, service_use, walkability_score, received_at`。日期字段使用 ISO 8601 格式；空单元格表示未知或未提供，不代表数值零，分析全程按缺失跳过。同一 `(area_id, period)` 允许出现多行：表示指标的迟到上报或事后修订，以 `received_at` 最新的一行为当前版本，旧行保留用于 as_of 快照复现（示例中 A-1 的 2026-Q1 服务使用量先报 310、后修订为 318）。
- `interventions.csv`：CSV 表格，字段为 `intervention_id, area_id, type, planned_start, actual_open, revision_of`。日期字段使用 ISO 8601 格式；空单元格表示未知或未提供，不代表数值零。`actual_open` 为空表示尚未实际开放，不产生开放效应分析；`revision_of` 填写被本条记录取代的旧干预编号，旧记录保留但标记为已取代（示例中 I-1-R 取代 I-1，开放日期由 5 月 20 日更正为 5 月 25 日）。
- `other-measures.csv`：CSV 表格，字段为 `measure_id, area_id, type, start_period, end_period`。期别使用 `YYYY-Qn` 格式；`end_period` 为空表示措施持续中。只落在单一比较组的措施会使对应结论降为"仅相关"。
- `area-history.csv`：CSV 表格，字段为 `boundary_version, area_id, block, residents, valid_from`。日期字段使用 ISO 8601 格式；`valid_from` 为该边界版本的生效日期，某一期别适用"不晚于该期别期初生效的最新版本"。版本切换导致区块集合增减时构成范围断裂，总量前后差不得直接参与趋势计算（示例中 B2 自 2026-04-01 生效，观察窗内即发生一次版本切换）。
