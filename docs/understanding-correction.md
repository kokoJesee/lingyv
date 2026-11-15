模块 7 更新（2026-11-05）：独立编辑/拒绝/删除自述已支持高优先级中断，显式消息内纠正及心理上下文已接通，见 [主流程集成](agent-support-integration.md)。下文为模块 5 原交付说明，其“待模块 7”内容以新说明为准。

# 模块 5：用户理解与纠正

状态：会话内理解管理服务、来源失效与离线演示已实现。没有新增 HTTP 路由、模型调用、自动长期记忆或心理诊断。

代码入口：`app/services/understanding_service.py`；操作契约：`app/schemas/understanding_service.py`。

## 运行演示

在仓库根目录运行：

```bash
.venv/bin/python -B -m app.understanding_cli
```

演示先记录“作息变晚”的虚构自述，提出并确认备考假设，再记录用户“其实是旅行”的纠正，编辑并重新确认。结果：旧确认版本 2 失效，当前版本为 4，只返回旅行解释；`memory_update` 始终为 null。
演示中的第一次确认是脚本模拟的用户操作，不表示 Agent 可以自行确认真实用户的理解。

## 服务接口

| 方法 | 结果 |
|---|---|
| `add_report(session_id, AddUserReport)` | 创建不可变的用户自述，服务端生成 ID，版本为 1 |
| `delete_report(session_id, report_id, DecideUnderstanding)` | 按预期版本删除自述，级联作废依赖理解 |
| `propose(session_id, ProposeUnderstanding)` | 提出理解，状态固定 proposed，生成当前确认令牌 |
| `edit(session_id, id, EditUnderstanding)` | 修改解释/来源，版本递增，撤销确认并换发令牌 |
| `confirm(session_id, id, ConfirmUnderstanding)` | 校验当前版本和令牌后确认；版本递增并清除令牌 |
| `reject(session_id, id, DecideUnderstanding)` | 拒绝并递增版本，不再作为当前理解 |
| `get(session_id, id)` | 控制界面查看当前对象及令牌；可能返回 rejected/invalidated 对象，不可直接当模型输入 |
| `current(session_id, scope=...)` | 仅返回当前、来源有效的 confirmed 理解；不含确认令牌 |
| `validate_reference(session_id, reference, scope=...)` | 检查当前理解的 ID/版本及用途；供后续提交方使用 |

用户自述的内容不会被解释编辑改写；纠正可创建一条新自述，再引用它编辑原理解。设备记录也不会因解释改变而改写。
`origin=user_report` 必须引用本会话真实存在的 user_report；`agent_hypothesis` 仍只是系统假设。显式确认表示用户认可自己的当前解释，不等于临床事实。
理解管理对象不接收外部指定的 confirmed 状态、session_id 或 memory_update；这些字段不能通过模型输出获得执行权。

例如：

```python
report = await service.add_report(sid, AddUserReport(content="其实最近是旅行，不是备考"))
edited = await service.edit(sid, understanding_id, EditUnderstanding(
    expected_revision=current_revision,
    interpretation="用户说明作息变化与旅行有关",
    origin="user_report",
    evidence_refs=[{"kind": "user_report", "source_id": report.report_id, "revision": 1}],
))
# edit 后旧 confirmed 已停用，必须等待用户明确确认：
confirmed = await service.confirm(sid, understanding_id, ConfirmUnderstanding(
    expected_revision=edited.understanding.revision,
    confirmation_token=edited.confirmation_token,
))
```

上例的 confirm 只能由明确用户操作触发。普通对话中识别纠正并发起编辑的逻辑尚未接入，不能用“还未确认”作为未来忽略本轮用户纠正的理由。

## 状态、依赖和重放

- 提出 v1 → 确认 v2 → 编辑 v3（proposed）→ 再确认 v4。旧版本和旧令牌均不能确认 v3/v4。
- 编辑同一理解、拒绝理解或删除其自述来源时，直接及间接依赖理解级联变为 invalidated；令牌与 confirmed_revision 清除。拒绝/作废对象不能直接复活，需提出新理解。
- 支持 user_report、wearable_record、consent 和已确认 understanding 的版本引用。跨会话、缺失、过期、重复和循环引用被拒绝；只允许引用当前已确认的父理解。
- 来源检查限制为 64 层、1000 次引用检查，超限拒绝，不静默丢弃部分来源。
- 设备来源必须获得用途授权；记录级引用保守要求该记录的所有字段在该用途可读取。当前 local_analysis 可读不表示 model_context 可读。
- 任意设备数据或授权变动都保守作废带设备依赖的理解及后代，包括授权扩大；必要时重新提出和确认。用户自述独立理解不受无关设备变化影响。
- 操作只缓存哈希、对象 ID、版本或错误，不缓存正文与确认令牌副本。同编号同内容可重试；结果已修订或作废时返回 operation_superseded，不回放旧解释。
- 控制界面可以查看作废对象供用户理解发生了什么；当前有效列表不会返回它。状态只保存当前修订，不维护旧版本正文历史。删除自述会移除自述正文，但解释对象保留作废状态；彻底清空本会话内容使用已有 clear_history。

分析结果如需转为解释来源，应使用其实际记录版本引用；本模块没有新增 analysis 类型的 EvidenceReference，也不会把一段无来源的分析文本当作有效来源。

## 会话与权限边界

所有写入使用已有会话锁；运行中、等待确认或生命周期操作期间返回 session_busy。当前模块不在模型生成中途自动提交理解纠正，回合内纠正与取消联动留到模块 7。

操作计数纳入共享会话预算。达到上限后仍允许 reject/delete_report，但不新增重放收据；预期版本检查仍有效。结束会话后不允许新建/编辑/确认，仅允许拒绝或删除自述。清空会话删除理解、令牌及自述，并将旧操作改为墓碑。

`current` 和 `validate_reference` 是内部读取接缝；后续调用方必须在同一会话锁内取值、检查并提交，不能把外部 JSON 或 get 的结果当作授权凭据。
用途检查目前覆盖设备依赖；自述属于用户显式输入，不自动变成新的后台收集/外发权限。怎样进入模型消息仍由后续 Runtime 决定。

现有 snapshot v1 不保存本模块状态，不恢复确认令牌。模型上下文、历史回复缓存、存档兼容和小镇动作的理解依赖尚未接通，分别在模块 7/8/9 实现。没有跨会话自动识别用户或长期记忆。

## 验证

新增 **16 项测试**，全项目 **433 passed**，1 项既有 Starlette/httpx 弃用警告。
覆盖备考→旅行、旧版本/令牌、幂等与旧结果拒绝、循环和级联、会话隔离、授权撤回、并发编辑、预算、结束/清空及深拷贝；离线 CLI 已实际运行。
这些是工程测试，没有真实模型、设备或心理效果验证。

```bash
.venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_understanding_service.py
```
