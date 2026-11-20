# Input material

All records are synthetic and refer to no real household, project, or city. A file is a source extract, not a precomputed answer. Preserve source identifiers and revisions when interpreting it.

- `area-periods.csv`: CSV; columns `area_id, period, residents, service_use, walkability_score, received_at`. Dates use ISO 8601 where present; blank cells mean unknown or not supplied, not numeric zero.
- `interventions.csv`: CSV; columns `intervention_id, area_id, type, planned_start, actual_open, revision_of`. Dates use ISO 8601 where present; blank cells mean unknown or not supplied, not numeric zero.
- `other-measures.csv`: CSV; columns `measure_id, area_id, type, start_period, end_period`. Dates use ISO 8601 where present; blank cells mean unknown or not supplied, not numeric zero.
- `area-history.csv`: CSV; columns `boundary_version, area_id, block, residents, valid_from`. Dates use ISO 8601 where present; blank cells mean unknown or not supplied, not numeric zero.
