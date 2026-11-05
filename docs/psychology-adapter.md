模块 7 更新（2026-11-05）：服务端可通过 PSYCHOLOGY_ENABLED=true 接入原消息入口，详见 [主流程集成](agent-support-integration.md)。下文为模块 6 原交付说明；专业审核仍未完成。

# 模块 6：心理接口适配与安全支持

状态：独立 Adapter、输入组装、结构化客户端接缝与工程策略检查已实现。**路线、转路规则、提示词和支持文案均为 unreviewed，尚未完成专业审核。** 没有把它接入默认聊天入口，也没有真实模型或临床验证。

## 运行离线演示

```bash
.venv/bin/python -B -m app.psychology --route routine_support
.venv/bin/python -B -m app.psychology --route clarify_safety
.venv/bin/python -B -m app.psychology --route safety_support
.venv/bin/python -B -m app.psychology --route urgent_real_world_help
```

每次使用相同的虚构输入和**指定的固定结果**验证工程通路，不进行真实分类。输出标记 unreviewed、live_model_calls=0，memory_update=null。这个演示不能证明安全路线识别能力。

## 代码与契约

| 文件 | 用途 |
|---|---|
| `app/schemas/psychology.py` | 可执行的 v0.1.0 Request / Result / Response |
| `app/psychology/inputs.py` | 从模型用途快照和当前理解组装输入，保留私有来源映射 |
| `app/psychology/adapter.py` | 一次模型调用、总超时、严格解析、来源检查、过期丢弃 |
| `app/psychology/policy.py` | 工程约束、待审核转路保护及无需模型的现实支持入口 |
| `app/psychology/prompts.py` | `psychology.draft.v1` 提示词及结果 JSON Schema |
| `app/psychology/mock.py` | 四条固定结果，仅用于工程测试 |
| `app/llm/client.py` | 新增 generate_structured，复用既有 DeepSeek HTTP 传输与错误检查 |

模型只生成 result；schema_version、session_id、request_id、session_revision 由程序从输入复制，不让模型猜测。未知字段、空白回复、重复 JSON 键、NaN、未知 route、重复/空引用和非 null memory_update 均拒绝。模型返回整个外层 Response 也会被拒绝。

既有两份 Request/Response JSON Schema 继续作为 v0.1.0 字段契约；新增 `docs/schemas/psychology-result.schema.json` 与 Python Result 模型保持测试同步。Schema 不能代替跨字段的来源或内容检查。

## 输入与用途

`PsychologyInputBuilder.prepare` 使用独立 `model_context` 用途快照进行模块 4 分析，读取模块 5 当前有效确认理解；没有相应用途授权时，只准备对话材料，不暗中使用 local_analysis 结果。

- 当前消息保留原话；历史由调用者显式提供，按所选顺序编号。不会把用户文本、设备数据或理解内容升级为 system 指令。
- 设备摘要最多 3 个证据组、6 项指标。显式 priority_metrics 优先，其次是询问候选，再按已有指标顺序；同组相关指标不作为独立证据加分。没有整体心理分数。
- 当前确认理解最多选最近更新的 4 条，沿用 confirmed_memory 标签，但正文明确它是用户确认的当前解释，不是临床事实或长期记忆。
- 未选观察/理解的数量在 PreparedPsychologyRequest 中返回；不是静默宣称材料完整。剩余材料仍可由本地服务读取。
- 来源编号只在本轮有效。记录真实 ID、版本及分析 ID 存在 source_manifest 中，不发给模型；模拟场景、评测期望和 family_id 不进入请求。
- 准备结果包含请求指纹、会话/设备版本。准备后篡改请求或来源/授权/理解更新、结束、清空，都使 validate_current 失败。

调用示例：

```python
builder = PsychologyInputBuilder(sessions)
prepared = await builder.prepare(
    session_id, request_id,
    current_message="其实最近是旅行，不是备考",
    history=[],  # 调用方显式选择；没有则明确为空
    as_of="2026-11-04T00:00:00+08:00",
)
response = await adapter.evaluate(
    prepared.request,
    ensure_current=lambda: builder.validate_current(prepared),
)
```

history 的旧回复依赖过滤、最近安全情境的保留，以及最终会话锁内的原子提交，仍须模块 7 接入。这里没有默认读取全部历史，也没有自动提交回复。validate_current 不能代替提交时的锁与运行生命周期检查。

## 路线工程保护

- 四路线均显式给出，不设默认安全路线。每次 evaluate 重新获得候选，不沿用上一轮结果假装成功。
- 非 routine_support 必须至少引用一个用户来源：当前消息、user 历史或 user_report。仅设备、hypothesis、assistant 历史或 confirmed_memory 不足以触发安全路线。
- 此检查只保证存在可定位的用户来源，**不证明引用语义足以支持所选路线**；当前性、隐喻、引用、第三人称等语义仍需专业规则和模型评测。
- 如果调用者传入上一条 urgent_real_world_help 且情境未解决，候选转出该路线被拒绝；未解决 safety_support 只允许保持或进入 urgent_real_world_help。拒绝意味着评估失败，不自动改写 route 或回复。
- 上一条为上述安全路线时，未提供 unresolved 默认按未解决处理。显式传入 unresolved=False 是未来 Runtime 的受控操作，不能从模型输出或客户端标记直接获得。
- 澄清后允许提出其他路线，但这只代表格式/工程流程可处理，不代表退出条件已获审核。目前没有经审核的“解决”判定器，不因一句谢谢自动解除情境。

未按关键词构建临床分类器，也未添加医学阈值。以上保守约束仍为工程草案。

## 故障、取消和支持入口

每次 evaluate 最多 1 次模型调用，无重试。上下文字符预算包含心理系统提示、结果 Schema 与完整请求；超限在调用前拒绝。调用超时使用现有 llm_timeout_seconds，输出长度默认最多 12000 字符（工程限额，待团队确认）。

重复键、非法结构、伪造引用、转路保护拒绝或模型失败都会抛出错误。**不把失败变成 routine_support，不返回一条假装评估成功的回复。**

PsychologyFailure 携带独立 support_notice，供未来 UI 在故障旁展示。`support_entry()` 无需模型、设备、草稿确认或表单即可取得现实支持入口描述；常驻入口与故障文案区分。
所有支持文案目前未审核，没有写死未经核验的热线/号码，也不会自动联系他人、发消息、设提醒或调用外部工具。

总超时使用独立等待边界；即使模型吞掉取消后返回迟到内容，也不会采用该结果。调用方取消则继续抛 CancelledError，不生成故障回复来覆盖取消。后台异常只被回收，不打印正文或 Key。

新增 DeepSeek 结构化方法复用既有配置和传输，使用心理模块 system prompt，不叠加通用聊天 prompt。已用 httpx.MockTransport 检查请求与响应；真实账户/网络调用未验证。

## 团队审核与后续接入

以下内容仍由心理/安全负责人审定，不由工程测试代替：四路线含义及反例、进入/退出条件、引用/过去事件/第三人称情境、允许/禁止措辞、故障和常驻支持文案、地域资源及输出长度。
当前代码使用 POLICY_VERSION=psychology_policy.draft.v1、REVIEW_STATUS=unreviewed；未伪造审核人、审核日期或通过记录。

模块 7 必须统一接入：会话串行与去重、active_task/取消传播、上下文来源过滤、已确认理解与可选设备数据、未解决支持情境、提交前再次校验以及完整成功后提交。默认 `/messages` 仍使用原聊天路径。本模块不设置 Session.state、WorkflowPhase 或 WAITING_CONFIRMATION。

## 验证范围

新增 49 项测试，全项目 **482 passed**（1 项既有 Starlette/httpx 弃用警告）。工程测试覆盖四路线、原始用例契约、字段拒绝、来源真实性边界、用途隔离、纠正前后输入、来源撤回、超时/取消后迟到丢弃、未解决情境转路、预算、无重试和模拟 HTTP 传输。
全部为虚构开发用例，不能作为独立心理效果评测。

```bash
.venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_psychology.py
```
