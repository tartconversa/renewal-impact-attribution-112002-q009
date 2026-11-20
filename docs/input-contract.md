# 合成输入材料

这里的记录均为合成数据，不对应真实住户、项目或城市。每个文件表示一个来源数据表，不包含分析结论；解释记录时应保留来源标识和修订关系。

- `area-periods.csv`：CSV 表格，字段为 `area_id, period, residents, service_use, walkability_score, received_at`。日期字段使用 ISO 8601 格式；空单元格表示未知或未提供，不代表数值零。
- `interventions.csv`：CSV 表格，字段为 `intervention_id, area_id, type, planned_start, actual_open, revision_of`。日期字段使用 ISO 8601 格式；空单元格表示未知或未提供，不代表数值零。
- `other-measures.csv`：CSV 表格，字段为 `measure_id, area_id, type, start_period, end_period`。日期字段使用 ISO 8601 格式；空单元格表示未知或未提供，不代表数值零。
- `area-history.csv`：CSV 表格，字段为 `boundary_version, area_id, block, residents, valid_from`。日期字段使用 ISO 8601 格式；空单元格表示未知或未提供，不代表数值零。
