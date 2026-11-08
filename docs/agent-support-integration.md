# 模块 7：Agent 主流程集成

日期：2026-11-05。状态：模块 1—6 已接入同一运行流程，离线端到端与 HTTP 接口验证通过；心理策略仍 unreviewed。真实模型、真实设备和心理效果没有验证。

## 直接运行离线闭环

```bash
.venv/bin/python -B -m app.support_cli
```

该命令实际走 AgentOrchestrator、数据/授权服务、分析、理解管理、输入适配、严格输出校验和事务提交；模型为明确标识的固定演示实现。

本次输出：4 次 fixture 调用，真实模型调用 0；“备考→旅行”纠正进入后续输入；撤回后旧回复无法重放，模型输入设备记录数为 0，旧助手回复索引 1/3/5 被排除。memory_update=null。
这验证工程链路，不是模型理解能力或心理效果评测。

## 启用同一个消息 API 的心理流程

普通聊天默认兼容保留。服务端显式设置 `PSYCHOLOGY_ENABLED=true` 后，原 `/sessions/{session_id}/messages` 使用新的支持流程，不需要客户端选择模型或提交 route。

```bash
PSYCHOLOGY_ENABLED=true .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`ANALYSIS_TIMEZONE` 默认 Asia/Shanghai。`/health` 显示是否启用和心理提示词版本。真实调用仍需要现有本地模型配置，本次未配置、修改或使用真实凭据。

一旦某会话进入心理流程，不能靠关闭配置降级回普通聊天；须清空或新建会话。心理会话不允许进入 `/workflow/demo` 的阻塞确认；从旧普通模式遗留的 demo 待确认状态切入心理消息时，会取消工程 demo，直接开始消息流程。现实支持入口 `/support` 始终独立，不依赖会话、设备或模型。

这些接口仍为本机单进程比赛演示；session_id 不是生产身份认证。

## 一轮执行与提交

1. 获取同一会话锁，检查生命周期、模式、请求编号及缓存依赖。
2. 按本轮显式纠正目标暂时排除旧理解及其后代；过滤来源已失效的历史助手回复，保留用户原话。
3. 使用 model_context 授权快照计算观察，加入当前有效确认理解和用户确认草稿。原始设备表、来源 sidecar、控制令牌和场景期望不发送给模型。
4. 一次 PsychologyAdapter 调用同时生成 route/reply 等候选；保留上下文字符、步骤、总超时和单次调用预算。
5. 校验结构、引用、路线约束、会话/授权版本和取消状态。
6. 完整成功后原子提交用户与助手消息、来源依赖、路线状态，以及本轮显式纠正产生的 proposed 理解；失败不部分提交这些内容。

返回值保留原 SendMessageResponse 字段，新增 `psychology` 和 `review_status=unreviewed`。PsychologyResponse 中 session_revision 为调用前快照版本；外层 revision 为本轮成功提交后的会话版本，这是有意保留的区别。

运行 phase=COMPLETED 只表示一次工程执行完成，不表示已经通过专业安全审核。Session.state 不因心理 route 被强行修改。

## 用户纠正

消息可带可选 `correction_of`，定位需要纠正的理解，无需先确认新版理解才能获得本轮支持：

```json
{
  "content": "其实最近是旅行，不是备考",
  "request_id": "22222222-2222-4222-8222-222222222222",
  "correction_of": {
    "understanding_id": "从理解接口读取的实际编号",
    "expected_revision": 2
  }
}
```

本轮输入即排除目标旧理解、相关后代理解和依赖旧理解的助手回复；当前纠正原话是最高优先级的用户输入。成功后以原话建立新自述，并把原理解修订为 proposed，换发确认令牌；后续显式确认才进入 confirmed。设备观测保持不变。

没有 correction_of 的自然语言消息仍原样进入模型，并由提示词要求优先尊重当前纠正；**尚未实现任意自然语言到具体理解 ID 的自动可靠匹配**，因此不会偷偷修改不确定的对象。显式编辑 API 是另一条可用的纠正路径。

如果消息生成失败，消息内纠正也不部分提交，调用方需展示失败并重试或使用独立编辑接口。同一失败 request_id 不重新执行模型，新尝试使用新编号。

独立 edit/reject/delete_report 在聊天生成中可高优先级中断：先验证目标和预期版本，设置屏障并取消 active_task，再持锁修改；旧版本、无效来源、重复操作及无变化编辑不会误取消新运行。普通 propose/report/confirm 在运行中仍拒绝 busy。

## 来源失效、历史与缓存

新心理回合记录 TurnDependencies：设备/授权版本、理解版本及草稿版本。依赖包含本轮提供的所有记录，而不只模型声称引用的来源；也继承所有选入的历史助手回复依赖。

设备/授权变化或理解修订后，直接及间接转述旧信息的助手回复均不再进入模型上下文。旧缓存在任何重放前检查，失效时删除缓存正文并保留 request_id 墓碑，返回 source_withdrawn，不重新执行模型。

原始历史消息保留用于用户查看，不据此恢复已撤回来源的使用权限；新模型上下文只选择有效助手回复。清空会话会删除消息、草稿、设备、授权、自述、理解、依赖和路线状态，旧请求变为清空墓碑。

这里保留普通聊天模式已有的草稿缓存重放语义；新的完整来源失效规则用于心理流程。普通聊天模式本来不注入设备或理解业务数据，已进入心理流程的会话也不能切回普通模式绕过过滤。

## 路线、故障与取消

上一条支持路线只在成功提交时更新。失败、取消或重放旧成功请求都不能解除未解决的 safety_support/urgent_real_world_help 状态；“谢谢”或换话题不是自动解除条件。专业审核的解决/退出流程尚待补充，当前保持模块 6 的保守转路保护。

心理失败保留独立 support_notice；首次失败和同编号重试展示一致通知，不把失败当 routine_support，不记录伪造成功回复。故障文案仍待审核，不自动联系任何人。

撤回、清空、结束、显式取消和有效独立纠正均可中断；即使底层模型吞掉取消后返回，迟到结果也不能提交。实际已经发给模型的数据无法靠本地取消追回；本轮测试只用了虚构数据与模拟模型。

## HTTP 接口

| 路径（会话项前缀为 `/sessions/{session_id}`） | 方法与用途 |
|---|---|
| `/messages` | POST，原消息入口，服务端选择普通/心理流程 |
| `/wearables/import` | POST，ImportWearables 批次；解析 JSON 前限制 4 MiB |
| `/wearables/consent` | POST，替换 provider 的指标与用途授权 |
| `/wearables/delete` | POST，按 record_id 和 expected_revision 删除 |
| `/analysis` | POST，as_of、timezone、scope；缺省 scope 为 local_analysis |
| `/reports` | POST，创建用户自述 |
| `/reports/{report_id}/delete` | POST，按版本删除自述 |
| `/understandings` | POST 提出理解；GET 当前有效确认理解 |
| `/understandings/{id}` | GET 控制视图；PUT 编辑 |
| `/understandings/{id}/confirm` | POST，版本与当前令牌确认 |
| `/understandings/{id}/reject` | POST，按版本拒绝 |
| `/context/preview` | POST，心理模式生成来源预览，不执行模型或提交纠正 |
| `/support`（无会话前缀） | GET，始终可用的现实支持入口描述 |

设备导入本身不授予任何用途。API 不提供直接将客户端 confirmed 状态或 route 写入会话的入口；确认和路由权限仍由程序管理。

## 存档边界

模块 8 已新增业务 snapshot v2。保存含设备、自述、理解或心理流程的会话，需要独立的 `save_business_data=true`；缺少选择返回 `archive_business_consent_required`。恢复设备记录不恢复用途授权，旧请求成为墓碑，确认凭据换发，支持路线与来源依赖保留。普通 v1 存档继续兼容。详见 [业务存档](business-archives.md)。

## 验证

新增主流程/API/中断测试包含：并发去重、显式与独立纠正、继承依赖、撤回重放、单独本地授权、五类中断及模型忽略取消、事务失败回滚、路线保持、故障通知重放、模式保护、存档拒绝、上传体积和完整离线演示。

```bash
.venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_support_runtime.py tests/test_understanding_interrupts.py
```

模块 7 验证（2026-11-05）：新增 39 项主流程/API/中断测试；仓库 tests/ 共 521 项通过。工作区另外收集到 reports/ 下 36 项本地验收探针也通过（这些忽略文件不计入仓库测试，不提交）。1 项既有 Starlette/httpx 弃用警告。实际运行离线 CLI，真实模型调用为 0；专业审核仍待完成。
