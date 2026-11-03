import argparse
import asyncio
import json
from pathlib import Path

from app.config.settings import PROJECT_ROOT
from app.evaluation.report import write_report
from app.evaluation.runner import load_suite, run_suite

DEFAULT_OUTPUT = PROJECT_ROOT / "reports"
LOCAL_PATHS_FILE = PROJECT_ROOT / "local.paths.json"


def default_output() -> Path:
    """Personal output preference is optional and never reads model credentials."""
    if not LOCAL_PATHS_FILE.exists():
        return DEFAULT_OUTPUT
    with LOCAL_PATHS_FILE.open("rb") as handle:
        raw = handle.read(16385)
    if len(raw) > 16384:
        raise ValueError("local paths file exceeds size limit")
    config = json.loads(raw)
    if not isinstance(config, dict):
        raise ValueError("local paths must be an object")
    value = config.get("evaluation_output_dir", "")
    if not isinstance(value, str):
        raise ValueError("evaluation output must be a string")
    if not value.strip():
        return DEFAULT_OUTPUT
    output = Path(value).expanduser()
    return output if output.is_absolute() else PROJECT_ROOT / output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="离线工程评测，不访问模型服务。")
    parser.add_argument("--suite", type=Path, default=PROJECT_ROOT / "evaluation/cases/runtime_v1.json")
    parser.add_argument("--output", type=Path, help="报告目录；优先于本地配置，默认项目 reports/")
    args = parser.parse_args(argv)
    try:
        suite = load_suite(args.suite)
    except Exception:
        print("案例集无效或无法读取；请检查格式、引用与大小。未运行评测。")
        return 2
    try:
        output = args.output if args.output is not None else default_output()
    except Exception:
        print("本地路径配置无效，请检查 local.paths.json 或用 --output 指定目录。未运行评测。")
        return 2

    def progress(index, total, case):
        print(f"[{index}/{total}] {case['id']}: "
              f"{'PASS' if case['passed'] else 'FAIL'} | "
              f"模拟调用 {case['model_calls']} | {case['elapsed_ms']:.1f} ms", flush=True)

    print("Agent 工程评测 | 离线模拟 | 真实模型调用 0 次", flush=True)
    try:
        report = asyncio.run(run_suite(suite, progress=progress))
        destination = write_report(report, output)
    except KeyboardInterrupt:
        print("评测已中断，未生成完整报告。")
        return 130
    except Exception:
        print("评测执行或报告写入失败；未标记为通过。请检查输出目录权限。")
        return 2
    summary = report["summary"]
    print(f"完成：{summary['passed']}/{summary['total']} 通过；关键失败 {len(summary['critical_failures'])}")
    print(f"报告：{destination / 'report.md'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
