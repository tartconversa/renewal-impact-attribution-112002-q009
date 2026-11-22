"""命令行入口。

用法：

    python -m renewal_analytics.cli run --data data --runs runs [--as-of 2026-08-01]
    python -m renewal_analytics.cli reproduce --manifest runs/<id>/manifest.json --data data --runs runs
    python -m renewal_analytics.cli compare --old runs/<id1> --new runs/<id2>
    python -m renewal_analytics.cli report --run runs/<id1>
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from .pipeline import run_analysis
from .report import render_comparison, render_markdown
from .versioning import compare_runs, load_run, reproduce, save_run
from .versioning import _to_jsonable  # noqa: PLC2701  内部复用序列化器


def _cmd_run(args: argparse.Namespace) -> None:
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    run = run_analysis(data_dir=args.data, as_of=as_of, run_id=args.run_id)
    run_dir = save_run(run, args.runs)
    (run_dir / "report.md").write_text(render_markdown(run), encoding="utf-8")
    print(f"运行完成：{run_dir}")
    print(f"结论包：{run_dir / 'result.json'}")
    print(f"中文报告：{run_dir / 'report.md'}")


def _cmd_reproduce(args: argparse.Namespace) -> None:
    outcome = reproduce(args.manifest, args.data, args.runs)
    print(json.dumps(outcome, ensure_ascii=False, indent=2))


def _cmd_compare(args: argparse.Namespace) -> None:
    comparison = compare_runs(load_run(args.old), load_run(args.new))
    out_dir = Path(args.runs)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"compare-{Path(args.old).name}-vs-{Path(args.new).name}"
    target.mkdir(parents=True, exist_ok=True)
    (target / "comparison.json").write_text(
        json.dumps(_to_jsonable(comparison), ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "comparison.md").write_text(render_comparison(comparison), encoding="utf-8")
    print(f"对比结果：{target}")


def _cmd_report(args: argparse.Namespace) -> None:
    result = load_run(args.run)
    # 报告渲染接受 dataclass；已落盘的结果用轻量对象重建其需要的属性视图
    from .pipeline import AnalysisRun
    run = AnalysisRun(**result)
    print(render_markdown(run))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="renewal-analytics",
                                     description="街道微更新分阶段影响评估（仅标准库）")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="执行一次完整分析并留痕")
    run_p.add_argument("--data", default="data", help="输入 CSV 目录")
    run_p.add_argument("--runs", default="runs", help="运行结果目录")
    run_p.add_argument("--as-of", help="数据快照截止日期 YYYY-MM-DD（默认全部已到达数据）")
    run_p.add_argument("--run-id", help="自定义运行编号（默认按日期生成）")
    run_p.set_defaults(func=_cmd_run)

    rep = sub.add_parser("reproduce", help="按旧 manifest 的口径复现历史分析")
    rep.add_argument("--manifest", required=True)
    rep.add_argument("--data", default="data")
    rep.add_argument("--runs", default="runs")
    rep.set_defaults(func=_cmd_reproduce)

    cmp_p = sub.add_parser("compare", help="并列对比两个运行版本")
    cmp_p.add_argument("--old", required=True)
    cmp_p.add_argument("--new", required=True)
    cmp_p.add_argument("--runs", default="runs")
    cmp_p.set_defaults(func=_cmd_compare)

    rpt = sub.add_parser("report", help="从已保存运行重新输出中文 Markdown 报告")
    rpt.add_argument("--run", required=True)
    rpt.set_defaults(func=_cmd_report)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
