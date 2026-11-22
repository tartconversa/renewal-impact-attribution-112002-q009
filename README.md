# Renewal Impact Attribution（街道微更新影响评估）

面向口袋空间改造、道路改善、社区服务点开放三类微更新的分阶段影响评估流程。
使用合成输入数据，仅依赖 Python 标准库（Python 3.12+）。

核心能力：

- **分阶段时间线**：按实际开放日期（而非计划）划分施工期/开放期，支持干预记录修订；
- **对照审查**：对照片区启动同类措施即审查退出，分批开放采用 not-yet-treated 对照；
- **分层估计**：朴素前后差（仅描述）→ 双重差分 → 多批次队列估计，附样本表与近似区间；
- **证据分级 A/B/C/D**：对照失效或资料不足时强制降级并说明原因，不输出确定性因果数字；
- **人口与边界**：范围断裂检测、持续区块同类变化、计数指标的数量/强度分解；
- **缺失不补零**：空单元格全程按缺失跳过，逐期披露有效样本与缺失率；
- **可复现可对比**：as_of 数据快照 + 输入指纹留痕，旧版本可复现、新旧结论并列查看。

方法细节见 [`docs/methodology.md`](docs/methodology.md)，输入字段见
[`docs/input-contract.md`](docs/input-contract.md)。

## 运行测试

```bash
python -m unittest discover -s tests -v
```

## 执行分析

```bash
# 一次完整运行，产物在 runs/<run-id>/（result.json、manifest.json、report.md）
PYTHONPATH=src python -m renewal_analytics.cli run --data data --runs runs

# 按历史时点复现（迟到修订会被排除在快照之外）
PYTHONPATH=src python -m renewal_analytics.cli run --data data --runs runs \
    --as-of 2026-08-31 --run-id aug-snapshot

# 复现旧运行并核对输入指纹；并列对比两个版本
PYTHONPATH=src python -m renewal_analytics.cli reproduce \
    --manifest runs/aug-snapshot/manifest.json --data data --runs runs
PYTHONPATH=src python -m renewal_analytics.cli compare \
    --old runs/aug-snapshot --new runs/<new-id> --runs runs
```

## 代码结构

```
src/renewal_analytics/
  periods.py      季度期别解析与日期归属
  loading.py      CSV 加载、缺失处理、as_of 快照、修订链
  timeline.py     干预状态矩阵、开放队列、对照资格审查
  population.py   边界版本、范围断裂、人口分解
  estimators.py   样本表、朴素差、DiD、队列估计、平行趋势、强度分解
  evidence.py     A/B/C/D 证据分级与降级原因
  pipeline.py     单次运行编排
  versioning.py   留痕、复现、指纹核对、版本对比
  report.py       中文 Markdown 报告
  cli.py          命令行入口
```
