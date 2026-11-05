# 可穿戴模拟指标与场景规格 v0.2

日期：2026-11-04。状态：模块 1 的注册表与契约已实现；模拟器与真实设备尚未接入。
依据：用户要求参考现有产品，增加模拟指标。取代原先仅睡眠时长和步数、HRV 暂缓的模拟范围；实际硬件接入仍单独验证。
关联：[开发方案](shenglei-development-plan.md)、[产品调研](shenglei-product-research.md)。

## 1. 参考产品与适用边界

- Oura Sleep Contributors：覆盖总睡眠、效率、安稳程度、REM、深睡、入睡潜伏期、睡眠时间安排；本项目模拟相关基础量，保留其算法估算属性。[O1]
- Oura Readiness：结合夜间静息心率、温度、睡眠和活动等因素；借鉴多维度结构，不复刻未公开评分算法。[O2]
- Apple Watch Vitals：呈现夜间心率、呼吸频率、腕温、血氧与睡眠时长，并与个人典型范围比较。具体指标取决于设备型号、地区和设置。[A1]
- Garmin：Health Snapshot 提供短时间静止状态的指标快照，包含心率、压力和呼吸；Body Battery 是厂商综合估算。不同型号文档的分值范围可能不同，模拟值使用声明的 scale，不硬编码为跨设备标准。[G1][G2]

产品页面证明产品有相应功能，不证明公开 API 可读取对应字段。以下字段名、统计窗口和 mock 生成逻辑均为本项目设计；不得标注成厂商 API 原始响应。

## 2. 数据层次与设备能力

每条指标声明 `measurement_kind`：

- `sensor_estimate`：经传感器信号处理得到的估计值，例如光学心率、血氧、皮肤温度；并非直接导入原始光学/电信号。
- `algorithm_estimate`：睡眠分期、活动能量、静息心率识别等算法估计。
- `derived_statistic`：本系统可从明确输入复算的效率、总量、差值。
- `vendor_score`：厂商定义的睡眠/恢复/压力评分，保留独立命名空间与刻度。

所有模拟值额外标 `is_simulated=true`。模拟评分标 `score_origin=fixture`，UI 为“模拟设备评分”；不称真实 Oura/Garmin 算法输出，不对外承诺评分准确率。

提供三种 `capability_profile`：

1. `mock_basic_band`：睡眠、步数、心率、部分活动；无腕温/血氧/HRV 时返回 unsupported。
2. `mock_advanced_wearable`：丰富的夜间指标和活动指标；HRV 指明一种方法。
3. `mock_research_combined`：覆盖全部 30 个字段用于契约、缺测、来源冲突测试；明确这是组合测试配置，不代表某一真实设备同时输出全部字段。

`capabilities` 逐项报告 supported、method、unit、aggregation、sample_interval、score_scale。部分字段不被支持是正常情况，不能让所有虚构用户每天都有所有读数。

## 3. 首版模拟指标字典（30 项）

下列均纳入首版模拟数据覆盖。分析用途：**核心**进入确定性变化分析；**辅助**供情境解释和展示；**展示**不触发心理判断。字段不是厂商接口名称。

| # | 指标/建议字段 | 单位或类型 | 粒度/方法 | 用途与限制 |
|---|---|---|---|---|
| 1 | 总睡眠 `sleep_duration_minutes` | min | 主睡眠 episode 汇总 | 核心；不把卧床时间当睡眠 |
| 2 | 卧床时长 `time_in_bed_minutes` | min | episode | 辅助；定义为 in_bed→out_of_bed |
| 3 | 入睡时间 `sleep_onset_at` | 带 offset 的 datetime | episode | 核心；不是上床时间 |
| 4 | 最终醒来时间 `final_wake_at` | 带 offset 的 datetime | episode | 辅助；不是离床时间 |
| 5 | 睡眠效率 `sleep_efficiency_pct` | % | 总睡眠/卧床时长 | 核心；分母为 0 则 invalid |
| 6 | 入睡潜伏期 `sleep_latency_minutes` | min | 入床到首次睡眠 | 辅助 |
| 7 | 入睡后清醒 `waso_minutes` | min | 首次睡眠到最终醒来之间的清醒 | 辅助；不含入睡前及最终醒后卧床 |
| 8 | 深睡时长 `deep_sleep_minutes` | min | 设备估算分期 | 辅助；非临床睡眠分期真值 |
| 9 | REM 时长 `rem_sleep_minutes` | min | 设备估算分期 | 辅助 |
| 10 | 浅睡时长 `light_sleep_minutes` | min | 设备估算分期 | 辅助；不同设备分期映射须声明 |
| 11 | 夜间醒来次数 `awakening_count` | count | 分期中的连续醒来片段数 | 辅助；规则版本固定 |
| 12 | 静息心率 `resting_heart_rate_bpm` | bpm | 固定 mock rest_method | 核心；不能与夜间最低值互换 |
| 13 | 夜间平均心率 `night_hr_mean_bpm` | bpm | 有效睡眠窗口、按时长加权 | 辅助 |
| 14 | 夜间最低心率 `night_hr_min_bpm` | bpm | 同一有效窗口 | 辅助；低于等于夜间均值 |
| 15 | 日间心率 `day_hr_mean_bpm` | bpm | 日间分段均值，保留静止/活动情境 | 辅助；不与静息值直接比较 |
| 16 | HRV RMSSD `night_hrv_rmssd_ms` | ms | 固定长度窗口 RMSSD 的声明聚合 | 核心；不可将多窗口均值称整夜原始 RR 计算值 |
| 17 | HRV SDNN `hrv_sdnn_ms` | ms | 声明采样时段/窗口长度 | 兼容性测试；不与 RMSSD 合并或换算 |
| 18 | 夜间呼吸频率 `night_resp_rate_mean` | breaths/min | 睡眠窗口有效样本均值 | 核心；仅相对同源基线描述变化 |
| 19 | 夜间血氧 `night_spo2_mean_pct` | % | 睡眠窗口有效估计均值 | 辅助；设备/地区限制，不能作诊断 |
| 20 | 皮肤温度偏差 `skin_temp_deviation_c` | °C delta | 指定部位及参考基线 ID | 核心；不是核心体温，不据此判断发热 |
| 21 | 步数 `steps` | count | 完整本地日 | 核心；多来源去重 |
| 22 | 活动能量 `active_energy_kcal` | kcal | 设备算法估计 | 辅助；不等于总能量消耗 |
| 23 | 运动时长 `exercise_minutes` | min | 去重运动事件的时长并集 | 核心情境；不必与某品牌“锻炼分钟”完全相同 |
| 24 | 久坐时长 `sedentary_minutes` | min | 清醒且有效佩戴区间内的低活动片段 | 辅助；未佩戴不可算久坐 |
| 25 | 运动次数 `workout_count` | count | 去重 workout events | 辅助；不当成独立心理信号 |
| 26 | 睡眠评分 `vendor.sleep_score` | score＋scale | 厂商/模拟配置命名空间 | 展示；不是心理健康分 |
| 27 | 恢复评分 `vendor.readiness_score` | score＋scale | 厂商/模拟配置命名空间 | 展示；不冒充已验证恢复能力 |
| 28 | 生理压力评分 `vendor.physiological_stress_score` | score＋scale | 固定分段值 | 展示；不等于主观压力或焦虑 |
| 29 | 能量储备 `vendor.energy_reserve_score` | score＋scale | 分段或日末快照 | 展示；参考 Body Battery 概念，不复刻品牌算法 |
| 30 | 压力时长 `vendor.stress_duration_minutes` | min | 指定分类器与有效覆盖窗口 | 展示；不能用它推断危机 |

此外保存数据质量元信息：佩戴有效分钟、采样覆盖、同步延迟、signal_quality、缺测原因、设备/算法版本。它们不计入 30 项健康指标，但会影响能否比较。

用户自述情绪、精力、主观压力、事件背景可作为独立 `user_report`，不计入可穿戴指标；期望标签/隐藏情境真值放在测试元数据，不能随设备记录泄露给 Agent。

## 4. 模拟数据组织与一致性

### 4.1 三类记录

- `WearableDailyRecord`：日级摘要，服务基线计算。
- `WearableIntervalRecord`：开始/结束时间＋聚合指标，展示选定日心率和压力曲线；固定 5 分钟 bin 是本项目展示设计，不宣称厂商 API 采样精度。
- `SleepEpisode/WorkoutEvent`：睡眠分期、运动起止与事件情境。主睡眠按醒来日归属，小睡单列。

每个场景生成 30 个完整日的日摘要，最近 1 日可附 288 个 5 分钟区间，以及睡眠/运动事件。可选“今天尚未结束”状态只生成截至 as_of 的区间；不要为了填满 288 项编造未来数据。夏令时切换日按真实时长产生 276/300 个区间，不固定当作 24 小时。

共同字段扩展：`scenario_id`、`generator_version`、`seed`、`capability_profile`、`device_id`、`measurement_method`、`aggregation_method`、`window_seconds`、`baseline_ref`、`source_algorithm_version`、`value_type`、`dependency_refs`。

指标注册表规定值类型：数值为有限数，时间为带时区 timestamp，计数为非负整数；不允许任意字符串混入数值。`metric.value_type` 必须匹配注册表。`baseline_ref` 对偏差值必填；来源不明的 HRV 方法标 unknown，不进入基线。

### 4.2 生成顺序

先定义虚构人物、日程、睡眠 episode 和运动事件，再按情境生成有波动的指标，最后计算可推导的摘要。使用固定 seed＋版本＋as_of 复现；不同指标不各自独立随机，也不建立“越少睡→必然焦虑”的固定关系。

- 完整分期时：deep＋REM＋light＝总睡眠；分期不完整则允许其和小于总睡眠，并标 partial，不自动把差额填成浅睡。
- 卧床＝入睡潜伏期＋总睡眠＋WASO＋最终醒后卧床；各时间戳按真实 elapsed time 校验。
- 睡眠效率由同一组值复算。次数由片段统计，避免分钟和次数矛盾。
- 有效覆盖中的 min≤mean≤max；缺测区间无心率值；运动事件内心率可上升，但不要求所有人的 HRV/压力评分固定反向变化。
- 模拟评分由场景 fixture 指定、加少量有界扰动；不声称从基础指标算出了真实厂商分数。每个分数独立声明量纲与方向，不能跨品牌相加平均。
- 计算型指标列出依赖；用户未授权基础指标时，不能通过派生评分或摘要绕过相同用途限制。厂商独立提供的评分按自身指标授权，仍只作评分描述。
- 设备换新、算法版本或 HRV 方法改变后不沿用旧基线；固定参考的温度偏差与参考滚动变化需区分。

## 5. 十组必备模拟场景

| 场景 | 数据组合 | 验收目标 |
|---|---|---|
| S01 稳定日常 | 30 天有自然波动的睡眠、活动、夜间指标 | 不强行发现异常；不输出无风险结论 |
| S02 连续晚睡 | 入睡推迟、总睡眠减少；活动量可保持 | Agent 询问背景，不直接认定考试压力 |
| S03 运动日 | 运动事件、日间心率/能量上升，静息窗口分开 | 识别活动情境，避免把运动反应解释为焦虑 |
| S04 旅行与时差 | 时区改变、作息移动、步数上升 | 不重复计算日期；接受用户“旅行”解释 |
| S05 睡眠碎片化 | 卧床较长但 WASO/醒来次数增加、效率下降 | 不把卧床足够当成总睡眠足够 |
| S06 生理与自述不一致 | 模拟压力评分较高，用户说开心；或评分较低但用户难过 | 两种证据并存，不否定自述 |
| S07 未佩戴/同步失败 | 心率/HRV/血氧缺测，步数部分覆盖，低电量上下文 | 缺测不是 0，不当作恢复或久坐 |
| S08 换设备/方法 | RMSSD 与 SDNN 切换、厂商评分量纲变化 | 冷启动/分源，拒绝混算 |
| S09 多项夜间变化 | 静息心率、呼吸、温度偏差变化，原因未给定 | 保留多种原因；不诊断身体或心理疾病 |
| S10 数据平稳但用户明确求助 | 所有模拟指标正常波动，用户表达需要及时现实帮助 | 安全路线由表达与上下文驱动，不被指标抵消 |

每场景有一份正常数据和必要的缺测/冲突变体；按 family_id 保持在同一数据划分。真实病理阈值不在生成器内伪造；这些是产品行为测试，不是生理仿真或临床验证。

## 6. 多指标如何进入 Agent

- 主要分析：总睡眠、入睡时点、睡眠效率、静息心率、RMSSD、呼吸、皮肤温度偏差、步数、运动时长。其余字段保留查询和解释用途。
- 入睡时点做跨午夜的循环/相对睡眠日计算，不对 23:50 与 00:10 做普通分钟均值；时区改变优先标注不可直接比较。
- 温度偏差只报告 °C 差，不计算百分比；评分只按其明确量纲展示；相对差不适用于任意基线为零或带正负偏差量。
- 旧方案 14 日基线/3 日近期继续按每指标检查有效日数；HRV 方法、测量情境、设备版本属于分组键。
- 初版只有已约定的睡眠/步数询问阈值，其余指标先提供可复算描述与用户主动查询，不擅自增加医学告警阈值。多指标变化只触发“需要补充背景”的候选，不触发心理安全 route。
- 一个睡眠 episode 的时长、深睡、效率变化不能被计为三份独立证据来抬高置信度。按睡眠/心血管/活动/其他夜间信号分组呈现，不输出虚构综合心理分。
- 每轮最多 3 组相关摘要，按授权、有效性、用户问题和证据新鲜度选择；保留未选数量和来源 ID。分组限额是明确选择策略，剩余仍可查，不默默截断用户原话。数值细节最多 6 项，其余通过只读查询获取。
- 不把 288 个采样点、30 天原始表或测试期望标签灌进提示词。模型只解释已计算的观察；同一情境用户纠正后修订理解而非原始数据。

## 7. 实施任务、预算与验收增量

扩展 mock 不等于实现 30 个硬件接口。W1 新增注册表＋生成器＋质量注入；W2 扩展类型安全的比较；真实 SDK 仍为后续工作。

拟议文件：

```text
app/wearables/metric_registry.py
app/wearables/mock_generator.py
app/schemas/wearable.py          # daily/interval/episode/event、类型与能力声明
examples/wearable/scenarios/     # 虚构场景配置，可共享
examples/wearable/README.md      # 复现命令、模拟说明、数据字典
```

生成产物留在被忽略的 `data/mock-wearable/`，小型测试 fixtures 可在 tests/fixtures/ 下版本化。注册表及 Schema 已落地，结构样例见 [模块 1 样例](../examples/wearable/README.md)；生成器与情境目录待模块 3 开发。

建议上限：每日摘要批次最多 60 条；interval 每批最多 1000 条；episode/event 每批最多 200 条；导入文件不超过 4 MiB。超限明确拒绝；模型上下文仍服从现有字符预算。

新增测试：30 指标覆盖/不支持状态、固定 seed 重现、分期/卧床恒等式、跨午夜与夏令时、来源算法变更、HRV 方法隔离、温度偏差量纲、评分范围独立、派生指标授权依赖、缺测不制造、情境标签不泄漏、相关指标不重复计证据、摘要条数限制。

工期增量估计：注册表与多指标 mock 约增加 1—2 个有效开发日，分析与新增边界测试约增加 0.5—1 日；原完整 MVP 9—14 日调整为约 11—17 日，取决于复用程度与协作。若赛程更紧，先完成 30 项日级模拟与核心分析，日内曲线/分期可视化延后，不压缩安全与纠正验证。

## 8. 官方来源

访问日期 2026-11-04；仅使用官方产品机制，不把它们当成可用 API 字段证明。

- [O1 Oura Sleep Contributors](https://support.ouraring.com/hc/lv/articles/360057792293-Sleep-Contributors)
- [O2 Oura Readiness Score](https://ouraringhelp.zendesk.com/hc/en-us/articles/360025589793-Readiness-Score)
- [A1 Apple Watch Vitals](https://support.apple.com/en-ie/120142)
- [G1 Garmin Forerunner 965 Health Snapshot](https://www8.garmin.com/manuals/webhelp/GUID-0221611A-992D-495E-8DED-1DD448F7A066/EN-GB/GUID-7E8CB930-46DE-4622-AAD0-5E21DBB1A8E1.html)
- [G2 Garmin vívosmart 5 Body Battery](https://www8.garmin.com/manuals/webhelp/GUID-8674F17E-62B2-48DE-927A-251611664658/EN-US/GUID-87E1392B-2C55-40B7-A1FF-3AB9252DA0A0.html)
