# LifeSnaps v4 演示选样

来源为 Yfantidou 等发布的 [LifeSnaps v4 数据包](https://zenodo.org/records/7229547)，许可为 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。[数据论文](https://doi.org/10.1038/s41597-022-01764-x)。

从发布包筛选四位参与者的连续 21 日窗口，仅保留当前适配器使用的日级设备字段。参与者以 LS01–LS04 标记，日期转换为相对日序。部分正值测量的源 0 值在选样时转为缺测，设备计数和入睡耗时的 0 保留。入睡耗时没有非零值，前端不展示。回放日历只用于展示，原时区未公开。

| 样本 | 特征 | 基线与近期中位数 |
| --- | --- | --- |
| LS01 | 近期睡眠缩短 | 392.5 → 306 分钟 |
| LS02 | 近期活动增加 | 8957.5 → 20670 步 |
| LS03 | 睡眠相对稳定 | 385 → 385 分钟 |
| LS04 | 近期睡眠缺测 | 最近三天只有 1 天有效睡眠值 |

第 1–4 日为图表背景，第 5–18 日为基线，第 19–21 日为近期。这些样本按展示特征筛选，用于开发演示。EMA 和 PANAS 未包含在本目录，也不作为 Agent 输入。

## 运行

仓库根目录启动后端，frontend 目录启动前端：

```bash
PSYCHOLOGY_ENABLED=true PSYCHOLOGY_DEMO_MODE=true LIFESNAPS_REPLAY_CSV=examples/lifesnaps/selected-wearable-daily.csv .venv/bin/python -B -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```bash
LOVESTONE_AGENT_API_URL=http://127.0.0.1:8000 npm run dev
```

此命令使用固定脚本。真实模型模式须配置后端凭据、设置 PSYCHOLOGY_DEMO_MODE=false 和 LIFESNAPS_LIVE_MODEL_ENABLED=true，并在发送时确认当前样本摘要。
