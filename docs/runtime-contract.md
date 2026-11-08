> 共享阅读快照；个人阅读原稿保留在负责人笔记中。

# Runtime V0.3 运行契约

Runtime 0.5.0 增加浏览器语音输入。上传、转写、取消、缓存与预算约定见 [语音输入说明](voice-input.md)。转写结果通过用户编辑和显式发送进入原有会话流程。

Runtime 0.6.0 将 DeepSeek 与 ASR 接入 [统一外部调用调度](call-scheduler.md)。共享总并发、供应商并发、有限等待队列和包括排队的调用总时限，GET /runtime/calls 提供当前进程的脱敏统计。

## 两种状态

Session.state 是业务阶段，现阶段保持 INITIAL。
Session.status 是生命周期：ACTIVE、ENDED。
Session.workflow 是当前运行快照，run_id 标识本次运行，phase 为：

| 当前阶段 | 合法操作 | 结果 |
|---|---|---|
| IDLE / COMPLETED / CANCELLED / FAILED | 活跃会话发送消息或开始演示 | RUNNING，新 run_id |
| RUNNING | 步骤成功 | 下一步骤或 COMPLETED |
| RUNNING | 失败/超限 | FAILED，明确 stop_reason |
| RUNNING | 用户取消、结束或清空 | 取消模型任务，丢弃迟到结果 |
| WAITING_CONFIRMATION | 提交当前 run_id 与 confirmation_token | RUNNING 后完成演示 |
| WAITING_CONFIRMATION | 取消/结束/清空 | CANCELLED 或重置 |
| WAITING_CONFIRMATION | 普通新消息、新演示或过期确认 | 409，不改变当前流程 |
| 任意阶段 | 结束会话 | ENDED，新请求不再执行 |
| 任意阶段 | 清空会话内容 | 消息清空、运行重置 IDLE；生命周期保持原值 |

新开始只允许从非活动运行状态进入。确认只适用于当前演示步骤，
不代表对模型内容、心理判断或个人数据用途的通用授权。

## API

| 方法与路径 | 用途 |
|---|---|
| POST /sessions | 创建会话 |
| GET /sessions | 列出本进程会话元数据，不返回正文 |
| GET /sessions/{id} | 查看会话与运行快照 |
| POST /sessions/{id}/messages | 发送 content，可指定 request_id |
| POST /sessions/{id}/end | 结束会话，重复结束无额外变更 |
| DELETE /sessions/{id}/messages | 清空消息和缓存回复 |
| POST /sessions/{id}/workflow/demo | 开始不访问模型的演示 |
| POST /sessions/{id}/workflow/confirm | 提交 run_id、confirmation_token |
| POST /sessions/{id}/workflow/cancel | 提交 run_id，取消对应运行 |
| GET /sessions/{id}/events | 查看有界运行事件，不返回正文 |

这是本机单进程开发接口，目前没有多用户身份验证；
加入多用户时，应先验证会话归属，再返回历史、去重结果或执行操作。

## 请求编号与重试

- request_id 在一个会话内标识一次消息提交。省略时由程序生成新编号。
- 相同编号、相同规范化内容：复用已经记录的结果，不再次执行模型。
- 相同编号、不同内容：409 request_id_conflict。
- 同一内容采用新编号：一次明确的新请求，允许再次执行。
- 失败也会记录；同一编号返回原错误。新编号表示用户主动重新尝试。
- 记录数量达到上限时明确拒绝新编号，保留已有去重保证。
- 清空会话时移除缓存回复，旧编号成为 request_cleared 墓碑，不恢复旧内容。
- 默认去重在当前进程、当前会话内有效。手动保存后恢复的旧编号会被阻止再次执行；最后一次快照之后的请求不在恢复保护范围，不提供跨崩溃执行恰好一次的保证。

## 提交、取消与进度

一轮先组装上下文、执行模型、校验结果，再一起提交用户消息和模型回复。
FAILED/CANCELLED 会更新运行诊断与 revision，但不修改之前已完成的消息和业务状态。
同一会话串行，不同会话可并行。结束/清空先阻止排队的新执行，再等待当前任务清理。
模型即使在取消后返回结果，程序也会丢弃。
客户端断开连接不等同于收到取消指令；需要主动取消时调用 cancel 并指定当前 run_id。
模型请求在上游是否已计费由供应商决定，本地取消不作退款或撤销上游调用的保证。

事件仅在内存保留最近 200 条，与对话分开，包含编号、阶段、步骤、原因、耗时、条数。
事件数量是诊断上限，不是会话对话上限。

## 预算与输出

配置中的 runtime_max_context_chars 包括静态系统指令和发送的对话文本。
这是字符长度检查，不是 token 计数；超过时返回 413，不裁掉旧消息。
runtime_max_steps 限制步骤推进；runtime_max_requests_per_session 限制去重记录。
llm_timeout_seconds 同时用于模型请求与整轮模型等待。
本轮没有自动模型重试、模型降级、摘要或输出续写。

parse_structured_output(text, schema) 负责严格 JSON 解码和 Pydantic 校验，
拒绝重复键、NaN、无效类型与目标 Schema 不接受的字段。
调用方通过 Schema 决定字段含义。格式通过不等于事实正确、心理判断合理或获得用户确认。

## 通用草稿扩展

草稿的版本、编辑、确认和拒绝机制见 [草稿版本管理](草稿版本管理.md)。
已确认草稿现已通过上下文组装进入模型；心理业务状态保持 INITIAL。

## 上下文与来源扩展

见 [上下文组装与来源记录](上下文组装与来源记录.md)。模型响应新增 context_manifest，另提供最近来源查看和只读预览接口。

## 可选本地存档扩展 V0.1

Runtime 0.4.0 新增显式保存、列表、恢复与按版本删除；启动和普通聊天继续默认内存。
保存时校验会话修订号与存档版本，SQLite 事务保证一致性；不保存 Key、确认凭据、事件或缓存回复副本。
恢复保留已完成对话、草稿和来源依赖，旧请求编号变为 request_restored；不重放未完成调用。
等待/运行阶段恢复后为 CANCELLED / restore_interrupted；待确认草稿换发凭据；已结束会话仍结束。
清空内存与删除磁盘存档是两项独立操作，具体接口和预算见 [会话保存与恢复](会话保存与恢复.md)。
