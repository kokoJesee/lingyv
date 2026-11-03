"""Write a complete run to a fresh directory; existing reports are retained."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


def render_markdown(report: dict) -> str:
    summary = report["summary"]
    status = "通过" if report["passed"] else "未通过"
    lines = [
        "# Agent 工程评测报告", "",
        f"结果：**{status}**；{summary['passed']}/{summary['total']} 个案例通过。",
        f"关键失败：{', '.join(summary['critical_failures']) or '无'}。", "",
        "模式：离线模拟；全部为虚构工程案例；真实模型调用 0 次。",
        "本报告验证程序行为，不评定共情质量、心理效果或风险识别能力。", "",
        f"- 案例集：{report['suite_id']} / {report['suite_version']}",
        f"- 运行器：{report['runner_version']}",
        f"- 环境：Python {report['environment']['python']}；Pydantic {report['environment']['pydantic']}；pydantic-settings {report['environment']['pydantic_settings']}",
        f"- 提示词：{report['prompt_version']}；上下文：{report['context_version']}",
        f"- 案例摘要（规范化 SHA256）：{report['suite_sha256']}",
        f"- app 源码摘要（SHA256）：{report['source_sha256']}",
        f"- 开始时间：{report['started_at']}（UTC）",
        f"- 总耗时：{report['elapsed_ms']:.3f} ms",
        f"- 时间上限：单案例 {report['case_timeout_seconds']} 秒，整批 {report['suite_timeout_seconds']} 秒",
        f"- 模型模拟等待上限：{report['model_timeout_seconds']} 秒", "",
        "| 案例编号 | 结果 | 关键项 | 已执行步骤 | 模拟调用 | 耗时 ms |",
        "|---|---|---|---|---|---|",
    ]
    for case in report["cases"]:
        lines.append(
            f"| {case['id']} | {'通过' if case['passed'] else '失败'} | "
            f"{'是' if case['critical'] else '否'} | {len(case['steps'])}/{case['planned_steps']} | "
            f"{case['model_calls']} | {case['elapsed_ms']:.3f} |")
    for case in report["cases"]:
        if case["passed"]:
            continue
        lines += ["", f"## 失败定位：{case['id']}", "", f"原因：{case['failure']}"]
        for step in case["steps"]:
            for check in step["checks"]:
                if not check["passed"]:
                    lines.append(
                        f"- 步骤 {step['index']} / {step['action']} / {check['name']}："
                        f"预期 {json.dumps(check['expected'], ensure_ascii=False)}，"
                        f"实际 {json.dumps(check['actual'], ensure_ascii=False)}")
    lines += ["", "各步骤的预算、预期值和实际值见同目录 results.json。",
              "未导出案例正文、消息、草稿正文、模型回复或本地配置。",
              "相同案例与源码可比较逻辑结果；时间、运行目录和标识每次可能不同。", ""]
    return "\n".join(lines)


def write_report(report: dict, output_root: Path) -> Path:
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    name = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:12]
    staging = output_root / ("." + name + ".tmp")
    destination = output_root / name
    staging.mkdir()
    try:
        (staging / "results.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (staging / "report.md").write_text(render_markdown(report), encoding="utf-8")
        staging.rename(destination)
    except BaseException:
        for filename in ("results.json", "report.md"):
            (staging / filename).unlink(missing_ok=True)
        staging.rmdir()
        raise
    return destination
