# 模块 1：可穿戴数据契约样例

这些文件均为**虚构结构样例**，不来自真实设备，不是模拟器输出或临床案例。
30 项字典见 `app/wearables/metric_registry.py`；产品语义见 `docs/shenglei-wearable-metrics.md`。

## 文件与复现

- `batch.json`：日级、区间、睡眠和运动四类记录；日级记录覆盖 30 项字段。
- `capabilities.json`：组合测试设备，显式声明全部 30 项；真实设备不保证具备这些能力。
- `analysis.json`：带来源版本的状态分析输出示例。不是对 batch.json 的计算结果，引用的 17 个记录是占位来源。
- `understanding.json`：系统提出、尚待用户确认的理解；不授予执行权或长期记忆写入权。

在仓库根目录运行：

```bash
.venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_wearable_contracts.py
```

也可直接校验导入结构：

```python
from pathlib import Path
from app.schemas.wearable import WearableImportBatch
batch = WearableImportBatch.model_validate_json(
    Path("examples/wearable/batch.json").read_text(encoding="utf-8")
)
print(len(batch.daily[0].metrics))  # 30
```

拒绝示例：将 `steps.value` 改成 `"100"`、`true`、`-1`，将单位改成 `kcal`，
删除时间中的 offset，或将分数改成超出自身 scale 的值，都会产生 `ValidationError`。
测试中包含这些变体，不需要复制多份坏数据文件。

## 契约选择

- Schema 版本为 `wearable.v0.1`、`state_analysis.v0.1`、`understanding.v0.1`。
- 单位统一用 `min`、`count`、`bpm`、`ms`、`%`、`breaths/min`、`degC_delta`、`kcal`、`score`、`timestamp`。
- 所有 timestamp 使用 ISO 8601 且带 offset，拒绝数字 epoch；保留原 offset，日归属另用 IANA timezone。窗口按实际经过时间比较，兼容 23/25 小时本地日。
- `MetricValue.window` 是聚合覆盖区间；`window_seconds` 是 HRV 等方法的采样窗口长度，两者不同。
- `valid` 与 `incomplete` 必须有值；`missing`/`unsupported` 必须为空；任何非 valid 质量必须给原因。`unknown`/`invalid` 可保留类型合法的原始值，但后续分析不得当作有效值。
- 计数只接受非负整数，数值拒绝布尔值、字符串、NaN 和 Infinity。边界仅为结构约束，不是生理告警阈值。
- 评分必须有 namespace、独立 scale 和 origin；模拟记录只接受 fixture 评分。派生统计须声明依赖，温度偏差须声明参考基线和部位。
- 日级完整记录结束于次日本地午夜，可包含前一夜睡眠窗口；`day_complete` 只表示时间区间完整，不代表每项指标佩戴覆盖完整。质量和 coverage 仍逐指标判断。
- 睡眠按最终醒来的本地日期归属；完整分期必须连续且与声明的总量一致。部分分期允许缺口，不自动补浅睡。
- 批次最多 60 个日摘要、1000 个区间、各 200 个睡眠/运动事件。4 MiB 文件上限待模块 2 导入服务落实。
- 输入不接受 owner/session_id，也不接受隐藏答案。通过 Schema 不表示已授权使用；引用存在性、数据归属、来源可比性、授权以及撤回失效均由后续服务校验。
- `Understanding` 的 confirmed 字段只检查结构一致性；不能将外部 JSON 的 confirmed 值当成真实用户授权，后续服务须绑定会话与确认凭据。

`docs/schemas/` 下导出的四份对应 JSON Schema 便于前后端联调。
JSON Schema 只能表达基本结构；跨字段的单位/指标一致性、时间关系、来源与状态约束，以 Python 模型校验为准。
当前文件是字段提案与工程实现，仍需团队接缝确认，尚未接入 Agent、模拟生成器或真实硬件。

## 模块 3 生成器

本目录静态 JSON 仍为模块 1 样例。生成 30 日连贯数据请使用 `python -m app.wearables`，详见 [模拟器说明](../../docs/wearable-simulator.md)。`scenarios/catalog.json` 是独立评测情境，不能随设备数据发送给模型。
