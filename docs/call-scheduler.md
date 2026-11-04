# 统一外部调用调度与预算

更新时间为 2026-11-04，随 Runtime 0.6.0 提供。开发前重读了 Harness 笔记 §2 的调度与级联取消，以及 §1 的有界执行和迟到结果作废。接口设计沿用 codebase-design 的小接口与依赖注入原则。

## 设计与归属

Agent 的文字对话会调用 DeepSeek，语音转写会调用腾讯云 ASR。两个入口共用 CallScheduler，集中分配执行名额、管理有限队列、检查总时限，并提供脱敏调用统计。

设计中的资源预算分为总并发、供应商并发、队列容量、排队时限和整次调用时限。文字输入长度、LLM_MAX_TOKENS、音频大小与时长继续由对应入口校验。token 使用量取供应商返回的数据，音频时长取已校验 WAV 的帧数，供观察和后续配置预算时使用。

本轮属于 Agent 工程开发。心理规则、路线、字段和案例标准继续通过团队接口评审确认。

## 接口与接入位置

核心接口如下。

~~~python
with call_scope(request_id, run_id):
    async with scheduler.slot("deepseek", timeout_seconds=45) as ticket:
        # 执行一次外部调用，校验其响应。
        ticket.record_tokens(provider_response.get("usage"))
~~~

slot 接收受控的供应商名称和本次总时限，名额在退出调用区间后释放。调用出错、取消或超时均走同一清理路径。DeepSeekClient.generate_reply 和 AudioService.transcribe 已完成接入，实际请求由各自的 Adapter 执行。

FastAPI 启动时创建共享实例，并注入聊天客户端和 AudioService。聊天的 request_id、run_id 通过 ContextVar 继承到模型任务，ASR 使用转写 request_id 和输入音频时长。终端的 DeepSeekClient 在自身实例内复用调度器。普通离线聊天、语音预设文字演示及缓存重放直接复用原结果。

服务停止时，调度器停止准入、唤醒排队请求并取消执行中的任务，等待任务清理。Adapter 应支持协作式取消。供应商调用发生后的实际计费以供应商记录为准。

## 配置与默认值

配置放在本地 .env，调整调度上限后重启服务。

~~~dotenv
EXTERNAL_MAX_CONCURRENT=4
LLM_MAX_CONCURRENT=2
ASR_MAX_CONCURRENT=2
EXTERNAL_MAX_QUEUE=16
EXTERNAL_QUEUE_TIMEOUT_SECONDS=5
~~~

| 配置 | 默认值 | 含义 |
|---|---|---|
| EXTERNAL_MAX_CONCURRENT | 4 | 聊天与 ASR 共享的执行名额 |
| LLM_MAX_CONCURRENT | 2 | DeepSeek 的执行并发上限 |
| ASR_MAX_CONCURRENT | 2 | 腾讯云 ASR 的执行并发上限，沿用语音模块的配置项 |
| EXTERNAL_MAX_QUEUE | 16 | 所有供应商共享的等待队列上限，设为 0 时只接受能立即获得名额的调用 |
| EXTERNAL_QUEUE_TIMEOUT_SECONDS | 5 | 排队最长等待时间 |
| LLM_TIMEOUT_SECONDS | 45 | 聊天调用总时限，包括排队 |
| ASR_TIMEOUT_SECONDS | 20 | ASR 调用总时限，包括排队 |

原有的会话生命周期、同会话串行、上传数量和请求记录上限仍参与准入检查。并发配置表示上限，实际同时执行数量还取决于其他检查和当前请求类型。

## 排队、公平性与超时

调度器扫描队列，先让最早且具备所需名额的请求开始。同一供应商保持 FIFO 顺序。某个 DeepSeek 请求因聊天名额已满而等待时，仍允许具备 ASR 名额和总名额的后续语音请求开始。

排队和执行共享同一截止时间。例如调用总时限为 20 秒，排队已等待 4 秒，则执行最多使用剩余的约 16 秒。聊天还继承 Orchestrator 给出的本轮截止时间。

每次开始调用前重新检查截止时间。排队取消或超时会移除队列项，避免随后开始调用。执行中取消会传播给 Adapter，名额在调用退出后释放。即使 Adapter 在收到取消后返回了结果，调度区间也会检查取消状态和截止时间，运行层继续检查该结果所属的会话和运行是否有效。

| 情况 | 对外错误与状态 |
|---|---|
| 队列已满 | 503 / external_queue_full |
| 排队超过等待上限或剩余总时限 | 503 / external_queue_timeout |
| 已关闭调度器 | 503 / external_calls_closed |
| 执行超过总时限 | 聊天映射为 504 / model_timeout，语音映射为 504 / audio_timeout |
| 用户取消 | 沿用聊天与语音入口的取消处理 |

失败与重试沿用现有去重约定。相同请求编号会复用已记录的失败，主动发起新尝试时使用新的请求编号。共享调度器每次只包围一次调用，重放成功或失败结果时不增加供应商调用次数。

## 查看运行统计

启动服务后，打开 http://localhost:8000/runtime/calls ，或在 /docs 中调用同名 GET 接口。

返回内容包括以下字段。

| 字段 | 内容 |
|---|---|
| scope | process，表示当前进程中的统计 |
| limits | 总并发、队列容量和最长排队时间 |
| active、queued | 已占用的执行名额与等待数量 |
| providers | 各供应商的并发上限、执行/排队数量、开始/完成/失败/取消/超时计数，以及用量累计 |
| inflight | 当前已准入调用的受控编号、状态和耗时 |
| recent | 最近 200 条结束记录，每条有 call_id、provider、request_id、run_id、状态、排队/执行耗时及受控用量 |

started 表示进入调用区间的尝试，供应商随后可能返回错误。统计会保留这些失败尝试。队列拒绝、等待超时和等待取消不会增加 started。

completed 表示外部调用区间成功退出，会话提交结果由原有 workflow 记录。

DeepSeek 用量只接受非负整数的 prompt_tokens、completion_tokens、total_tokens，并校验合计关系。供应商缺少或返回不一致的用量时，token_usage 为 null，累计 token_usage_missing_calls。有效用量累计到 token_usage_reported_calls 和对应 token 字段。回复校验失败时，已收到的有效用量仍然计入。[DeepSeek 响应字段说明](https://api-docs.deepseek.com/api/create-chat-completion/)

ASR 的 input_audio_seconds 表示进入调用区间的已校验输入长度，可用于观察提交量。它与供应商的计费时长分别记录。模拟转写和缓存重放跳过外部调用统计。

统计在本机内存中保留，进程重启后重新开始。记录只包含受控元数据和用量，提示词、转写文字、回复、凭据、HTTP 头、音频和异常 traceback 由各自输入处理流程持有，不写入这份统计。

当前服务用于本机单用户开发。多人部署时，统计接口与其他运行接口一起接入身份和访问权限检查。

## 验收

新增检查通过真实客户端和模块入口注入模拟 HTTP、模拟 ASR，验证共享总名额、供应商上限、FIFO、跳过暂不可执行请求、有限队列、等待超时、执行截止时间、取消竞态、关闭与去重。

另覆盖供应商用量缺失/错误、回复校验失败仍记录用量、最多 200 条历史、统计副本隔离、模型任务关联请求和运行编号，以及排队失败后会话内容保持一致。验证结果在开发记录和 README 更新。

2026-11-04 新增 22 项调度检查通过，全项目 237 项测试与 14/14 工程评测通过，回归环境为 Windows / Python 3.13.5。

本次调度验证使用虚构输入与模拟供应商。新增模块仍须通过同一接口维护相关不变量。
