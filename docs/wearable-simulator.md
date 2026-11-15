# 模块 3：多指标可穿戴模拟器

状态：离线生成、回放、文件导出和模块 2 导入已实现。所有记录都是虚构数据，不是设备 API 输出、生理仿真或临床验证。

## 运行

在仓库根目录执行，输出目录必须不存在，避免覆盖上一次结果：

```bash
.venv/bin/python -B -m app.wearables \
  --scenario S02 --seed 42 \
  --as-of 2026-11-04T00:00:00+08:00 \
  --profile mock_research_combined \
  --output data/mock-wearable/my-s02-run \
  --import-demo
```

默认生成 30 个完整日：30 日摘要、30 个睡眠事件、30 个运动事件、30 个活动来源区间，以及最后一天的 288 个五分钟区间，共 408 条记录。
`--import-demo` 在新建的临时内存会话中通过模块 2 导入，不自动授予任何用途；程序退出后该会话消失。
省略该参数仅生成文件。`--no-intervals` 关闭五分钟曲线，但保留日级派生量引用的活动来源区间。

可选参数：`--days 1..60`、`--timezone Asia/Shanghai`、`--variant normal|missing|source_conflict`。
`as_of` 必须显式给出 offset，不读取当前时间。首版只生成完整日；尚未提供“今天尚未结束”的日摘要。任何输出记录都不晚于 as_of。

## 三档能力与十场景

- `mock_basic_band`：睡眠、心率和活动；HRV、呼吸、血氧、温度及部分厂商评分明确标 unsupported。
- `mock_advanced_wearable`：丰富夜间指标，普通设备使用 RMSSD；更换设备后改用 SDNN，不同时冒充两种方法。
- `mock_research_combined`：覆盖 30 个字段的组合测试配置，不代表某一款真实设备。S08 的旧 RMSSD 在新设备上仍明确 unsupported。

| 场景 | 最近三个完整日的变化 |
|---|---|
| S01 | 有波动的稳定作息 |
| S02 | 总睡眠减少约 90 分钟，醒来时间不变，因此入睡推迟 |
| S03 | 运动从 20 分钟变为 60 分钟，步数、能量和日间心率联动 |
| S04 | 切换到 Asia/Tokyo、步数增加；日期唯一并保留每条记录的时区 |
| S05 | WASO 增加、四段独立清醒、卧床变长和效率降低 |
| S06 | 模拟压力评分较高，自述“开心”只在独立评测文件中 |
| S07 | 夜间读数缺失、部分佩戴、部分睡眠分期缺失 |
| S08 | 设备/算法/温度参考变化，RMSSD 切换 SDNN，评分量表由 0—100 改为 0—5 |
| S09 | 静息心率、呼吸、温度同时发生模拟变化，不提供疾病标签 |
| S10 | 与同 seed 的 S01 设备数值相同，明确求助只在独立评测文件中 |

每场景都有 normal、missing 和 source_conflict 变体。missing 将最近三天加入缺测与部分佩戴；source_conflict 表示来源/方法不连续，使用第二台设备，仍是合法结构，不故意制造坏 JSON。所有变体归同一 family_id，后续评测划分不得把同族拆到开发集与独立测试集。
场景目录见 `examples/wearable/scenarios/catalog.json`；场景的生成规则在 `app/wearables/mock_generator.py`。

## 数据关系与来源

1. 先构造睡眠与运动事件，再计算睡眠总量、分期、效率和运动总量。完整分期满足深睡＋REM＋浅睡＝总睡眠；卧床包含潜伏期、WASO 和醒后卧床。
2. 缺失分期不补成浅睡，保留 partial staging 原因；设备估计的整夜总睡眠与部分可用分期分别表达。
3. 日级睡眠效率引用睡眠事件，运动分钟和次数引用运动事件；步数、能量、久坐引用活动来源记录。来源 ID 与版本均可在同批中找到，没有自引用。
4. 五分钟曲线按 UTC 实际经过时间迭代，春季/秋季切换日分别为 276/300 点；日级时间窗使用 IANA 时区，睡眠归醒来日。旅行场景是本地日与时区切换的边界样例，**不模拟实际航班轨迹，也不保证相邻异时区日窗在 UTC 中无重叠**；后续分析须分时区，不将两个日窗直接累加。
5. 未佩戴区间没有心率读数；部分日步数、能量和久坐带 incomplete 与 coverage，不能当完整日比较。运动窗口心率提高，静息和夜间窗口分开。
6. 评分是 fixture 值，压力曲线有固定种子的有界扰动。评分保留独立 namespace/scale；不复刻厂商算法，不产生心理诊断或安全路线。

当前采样与分期都是便于检验的简化形状，每天有一个步行事件；不存在真实人群参数拟合。coverage 是场景设定，不是传感器质量实测。尚未生成独立的信号质量、设备电量或同步延迟序列。

## 回放与评测隔离

输出文件分为：

- `batch.json`：唯一可导入设备服务的批次，不包含场景名称、期望行为、用户自述或 family_id。
- `capabilities.json`：每个虚构设备支持/不支持的指标、方法、量纲和评分刻度。
- `replay-manifest.json`：generator_version、seed、as_of、设备能力档、场景、变体和 family_id。仅供回放，不送入 Agent。
- `evaluation-only.json`：虚构用户自述与期望行为，仅供评测或演示时显式输入对话，不自动作为设备观测。

相同代码版本及全部参数生成相同记录和 ID。ID 为参数摘要＋记录类型/日期，不含场景名称。改变参数会生成不同的批次 ID；目前不提供跨查询滚动更新的稳定设备 ID 映射，重演同一批用于幂等验证。
生成数据默认目录 `data/mock-wearable/` 被 Git 忽略。需要复现时保存清单，不提交完整个人数据式输出。

## 接口

`generate(MockQuery)` 返回批次、能力、回放清单、评测材料四部分。
`MockWearableAdapter.fetch(query)` **只返回批次**。
`await adapter.import_into(service, session_id, query, expected_revision=..., operation_id=...)` 使用模块 2 原子导入，不修改用户授权。

当前 `MockQuery` 是有上限的同步生成请求，无外部网络和模型调用；生成失败不会修改会话。数据只有在生成完整且通过契约校验后才能提交导入。

## 验证

```bash
.venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_wearable_generator.py
```

覆盖三档×十场景、缺测与来源变体、固定种子、睡眠/活动恒等式、夏令时、HRV 与量表切换、隐藏标签隔离、模块 2 导入幂等与默认未授权、命令行导出及防覆盖。
这些是生成器工程测试，不是 Agent 效果、心理安全或独立真实用户评测。变化分析留给模块 4。
