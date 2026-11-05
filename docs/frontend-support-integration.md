# 持续支持流程：前后端协作

2026-11-05。保留上游电影感页面、本地演示和一次性联网模式，新增独立“后端支持流程”。该模式沿用模块 1—7 的会话、来源、用途授权与确认机制。业务存档见 [模块 8](business-archives.md)。心理规则和措辞仍 unreviewed。

## 本机启动

无需真实 Key 的工程演示。在仓库根目录的终端 1 启动后端：

```bash
PSYCHOLOGY_ENABLED=true PSYCHOLOGY_DEMO_MODE=true ASR_PROVIDER=disabled .venv/bin/python -B -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另开终端 2，在仓库根目录启动前端：

```bash
cd frontend
npm ci
LOVESTONE_AGENT_API_URL=http://127.0.0.1:8000 npm run dev
```

Windows PowerShell 可先设置 `$env:PSYCHOLOGY_ENABLED="true"`、`$env:PSYCHOLOGY_DEMO_MODE="true"`，再用 `.venv\Scripts\python.exe` 启动；前端设置 `$env:LOVESTONE_AGENT_API_URL="http://127.0.0.1:8000"`。配置也可以写入本地忽略文件，勿提交 Key。

固定脚本调用真实的后端授权、分析、理解服务与原子提交，但回复不是实时模型推断。脚本展示当前输入、按输入优先顺序选取的一条当前确认内容，以及实际获准进入模型的设备观察组数；不会从文字分类风险。确认内容优先使用理解条目，最近更新的已确认理解在前；通用草稿排在理解之后，没有理解时遵循草稿输入顺序，不声称它是跨类型按时间最新的记录。固定路线只由服务端 PSYCHOLOGY_DEMO_ROUTE 配置，默认 routine_support；其余路线仅供受控工程测试。

真实模型联调须显式设置 PSYCHOLOGY_DEMO_MODE=false 并配置本地模型凭据，页面会显示不同模式。没有自动失败回退为模拟成功。两种模式均只使用虚构内容验收。

## 稳定接缝

- 浏览器只请求 Next 同源 `/api/support/*`；Next 代理限制本机上游、路径/方法/查询白名单、输入体积与等待时间，不传模型 Key。沿用该代理无需开放 FastAPI CORS。
- 一段旅程保持一个 session_id。代理不会在每条消息后新建/清空/结束。刷新页面不会自动删除后端内存；进程重启会丢失未保存的会话。
- 请求编号由调用端持有：不确定的网络结果用同一编号取回；用户明确新尝试才换编号。停止先校验 active_request_id，再按本轮 run_id 调 cancel。断开网络不等于后端收到取消。
- 代理保留 psychology.result、review_status、workflow、context_manifest 及受控错误码/support_notice；风险路线控制支持呈现，独立现实支持入口不依赖画布确认。
- 心象画布后端模式展示理解条目、来源、版本与不确定性。编辑/确认/拒绝使用后端理解版本和确认令牌；现有四栏结构仍属于明确标注的本地演示。

## 新增读取接口

| 接口 | 结果 |
|---|---|
| GET /wearables/catalog | 指标 ID/单位/方法与公开模拟样本编号，不含隐藏情境答案 |
| GET /sessions/{id}/support-state | session、active_request_id、支持路线、有效 proposed/confirmed 控制视图、设备版本与用途概要 |
| GET /sessions/{id}/understandings/control | 可发现的有效待确认/已确认理解 |
| POST /sessions/{id}/wearables/simulate | 按 scenario_id/seed/as_of/days 生成并显式导入，不自动授权 |
| GET /sessions/{id}/wearables/trends | local_analysis 用途过滤，7/14/30 日序列，缺失保持 null，冲突来源不拼接 |

用途授权沿用 /wearables/consent，分别选择 local_analysis 和 model_context。/analysis 继续提供基线/近期中位数和不足原因。图表显示睡眠小时由分钟换算；HRV明确RMSSD，不能与SDNN混用。读取状态不等待正在运行的模型锁，便于轮询和取消。

## 人工验收流程

1. 选择后端支持流程，检查固定脚本标识，开启旅程。
2. 身心状态导入 21 天模拟记录，先只授权页面分析；图表有值，但对话设备观察仍为 0。
3. 另外允许对话使用，下一轮显示设备观察；撤回后再发消息应为 0。
4. 心象画布提交“最近备考”、确认；在聊天中指定纠正目标并改为“最近旅行”，核对新版理解需重新确认；确认后下一轮采用旅行内容。
5. 显式勾选并保存。进程重启后恢复，设备用途为空、旧确认凭据不能复用，支持路线保留。
6. 单独删除测试存档、清空测试旅程；核对两者不会互相冒充。

源代码自动检查与真实模型、真实设备、心理效果评测分开记录；浏览器验收使用虚构输入和固定脚本。

## 本次验收记录（2026-11-05）

| 层级 | 已验证结果 |
|---|---|
| 后端回归 | `python -B -m pytest -q -p no:cacheprovider tests`：615 passed，1 项既有 Starlette/httpx 弃用警告 |
| 前端自动检查 | `npm test`：27 passed；`npm run typecheck`、`npm run build` 通过。默认 Turbopack clean build；本机增量缓存曾导致 Node 子进程路径错误，隔离生成缓存后通过，未改生产脚本 |
| 实际代理 | Next 生产预览到 FastAPI，创建 201、清空/结束 200；补充空请求流回归，修复新建会话 415 |
| 授权闭环 | S02、seed=42、21 个完整日共 84 条虚构记录；仅 local_analysis 时有图表、对话观察为 0；加 model_context 后为 6；撤回后为 0 |
| 理解闭环 | 备考自述提出 v1、确认 v2；聊天中改为旅行生成待确认 v3、确认 v4；下一轮引用旅行。提交成功后输入与旧纠正目标清空；提出理解成功后表单清空 |
| 存档恢复 | 未勾选保存时按钮不可用；保存后重启后端进程，从磁盘恢复记录与纯自述理解，local_analysis/model_context 均为 false；删除磁盘存档后当前内存内容仍可查看，另行清空成功 |
| 支持入口与页面 | 在理解尚未确认时可展开独立现实支持入口；1440px 桌面和 390px 手机宽度检查趋势/授权/基线排版，桌面检查画布。入睡时间显示“前一日 23:09 → 当天 00:33” |

浏览器使用本机生产构建、固定脚本和独立临时 SQLite，验收存档与内存内容已清理。打开仍在内存的会话时后端按契约返回 409，页面打开当前内容而不覆盖；远程背景视频曾出现连接失败，但业务页面及本地海报可用。截图与本机日志保留在被 Git 忽略的 `reports/integration-20261105/`。

真实模型、真实设备调用均为 0。未将浏览器检查称为真实手机测试，也未在浏览器逐一重演所有取消竞争及安全路线；这些分支由工程测试覆盖。模块 9 的完整版本化动作契约、模块 10 的独立心理效果评测和专业审核仍待完成。
