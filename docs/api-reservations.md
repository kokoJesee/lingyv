# 接口与字段预留清单

状态：v0.2（已写入规划）
用途：在还有大量模块没定的时候，先把**加了就难改**的地方留好口子；以后只加字段、只加接口，不改语义。
配套：[画布设计](canvas-design.md)、[长期记忆设计](long-term-memory.md)

---

## 0. 三条原则

1. **只加不删**：字段只加可空带默认；接口只加新路径；已发布语义不改。
2. **从第一天就带 owner**：所有新表/新接口都带 `owner_id`，现在恒为 `local`，账号上线时不用迁移。
3. **预留位要么 501、要么默认关闭**：不留"看起来能用其实没实现"的假接口；预留必须能被探测到（`/health` 里有开关状态）。

---

## 1. 现在就要留（画布 M1 落地前定，最省事）

### 1.1 HTTP 层

| 项 | 怎么留 | 为什么现在 |
|---|---|---|
| **列表分页** | 新列表接口统一支持 `?limit=&cursor=`，本期忽略 cursor、默认返回全量；响应先用数组 | 以后加游标不用改前端契约 |
| **501 预留端点** | 画布：`POST /canvases/{id}/draft`、`POST /canvases/{id}/cards`、`POST /records/{id}/append`、`GET /canvases/{id}/export`；记忆：已有 `POST /memories/propose`；角色：`GET /personas` | 前端可以照着路由把按钮和空态先接上，不用等后端 |
| **错误码命名空间** | 现有 `code` 字段沿用；新增前缀 `canvas_* / record_* / card_* / account_* / persona_*` | 前端按前缀降级，不用逐个枚举 |
| **健康检查带模块状态** | `/health` 加 `modules: {canvas: "enabled|disabled|reserved", cards: ...}` | 前端和运维能判断"这个功能现在有没有" |
| **幂等** | 写接口统一接受 `request_id`（已有 `SendMessageRequest` 先例） | 网络重试不会双写 |

**不做的**：`/v1` 版本前缀（全是内部客户端，加了只是噪音）、GraphQL、OpenAPI 之外的 SDK。

### 1.2 数据字段（画布/记录/卡片，M1 建表时一次性带上）

**canvases**

| 字段 | 本期取值 | 将来用在哪 |
|---|---|---|
| `owner_id` | `local` | 账号系统 |
| `schema_version` | `canvas.v1` | 结构演进 |
| `persona_id` / `draft_profile` / `prompt_version` / `model` | 起草时写，手填为空 | 角色化起草、可复现 |
| `sensitivity_json` | `{level, categories}` | 敏感提示、记忆跳过 |
| `enc_key_id` | `k1` | 密钥轮换 |
| `merged_from_json` | `[]` | 画布合并（二期） |
| `export_version` | `1` | 导出格式 |

**entries（lanes 内）**：`entry_id`、`source`、`confirmed`、`origin`、`risk_tags[]`、`lang`、`embedding_ref`（空，向量检索二期）。

**records**：`version`、`superseded_by`、`observed_at`、`recorded_at`、`cards_json`、`sensitivity_json`、`tags[]`、`export_version`、`enc_key_id`。

**cards（M2 建表时）**：`card_id`、`record_id`、`canvas_revision`、`source{lane, entry_id}`、`kind`、`status`、`prompt_version`、`owner_id`、`schema_version`。

### 1.3 事件总线（进程内，先不用消息队列）

画布不直接 import 记忆/档案，只发事件；预留统一入口：

```python
# app/events.py（新增，M1 只做同步内存版）
publish("canvas.confirmed", payload)
subscribe("canvas.confirmed", handler)
```

| 事件 | 现在有没有用 | 将来谁听 |
|---|---|---|
| `canvas.confirmed` | 档案刷新 | 记忆提议、提示语生成 |
| `canvas.draft_completed` | — | 前端、质量指标 |
| `record.superseded` / `record.deleted` | 上下文失效 | 卡片重建、导出 |
| `memory.proposed` / `memory.confirmed` | — | 设置页、评测 |
| `support.route_changed` | 已有路线概念 | 记忆守卫、画布敏感提示 |
| `wearable.imported` | 已有 | 档案"那天还有"摘要 |

**不做的**：外部消息队列、跨进程总线（单机单进程够用）。

### 1.4 存储升级骨架（比字段更隐性，但更贵）

现在三个库（记忆、画布、存档）各自 `PRAGMA user_version`，但**没有迁移阶梯**——只有 `CREATE TABLE IF NOT EXISTS`。

M1 前补一个最小迁移器：

```python
MIGRATIONS = {1: _v1, 2: _v2}   # 版本 → 变更函数
def migrate(conn):              # 读 user_version，逐阶执行
```

以后加字段只写一个迁移函数，不用手工改库、不用清数据。

### 1.5 加密密钥标识

每条密文旁边存 `enc_key_id`（现在恒 `k1`）。将来换密钥时新旧共存、按 id 解密、后台重加密；不存 id 就只能全库一次性重写，风险高。

---

## 2. 前端 / BFF 预留

| 项 | 现状 | 预留做法 |
|---|---|---|
| BFF 代理路径 | 只有 `/api/support/*` 透明转发 | 加 `/api/canvas/*`、`/api/records/*`、`/api/memories/*` 同款转发；或改成一份白名单表 |
| 列表响应形状 | 数组 | 新接口先数组 + 可选 `?limit=&cursor=`；暂不强制 envelope |
| 页面开关 | 无 | 前端读 `/health.modules` 决定按钮显示，不硬编码"这个功能还没做" |
| 免责声明 | 有"本地演示" | 文案里避免提前承诺账号/云同步（已经有这个问题） |

---

## 3. 配置开关清单（预留 = 默认关闭或安全默认）

| 开关 | 默认 | 归属 |
|---|---|---|
| `PSYCHOLOGY_ENABLED` | false | 已有 |
| `MEMORY_ENABLED` | true | 已有 |
| `CANVAS_ENABLED` | true | M1 |
| `CANVAS_ENCRYPTION` | on | M1 |
| `CANVAS_DRAFT_PROFILE` | `shiguang` | M1 |
| `CARDS_ENABLED` | false | M2 |
| `PROMPT_SUGGEST_ENABLED` | false | M2 |
| `MEMORY_PROPOSE_ENABLED` | false | M2 |
| `ARCHIVE_MONTHLY_ENABLED` | true | M2（月视图） |
| `ACCOUNT_ENABLED` | false | M3 |
| `EXPORT_ENABLED` | false | M2 |

规则：**新模块上线前默认关闭**；只有"不改变现有行为"的才允许默认开（画布就属于这类：没画布时不注入）。

---

## 4. 模型与提示词契约

| 项 | 预留 |
|---|---|
| 结构化输出 | 所有 LLM 输出带 `schema_version`，解析器容忍未知字段（`extra="ignore"`） |
| 提示词 | 每个生成点独立 `prompt_version`（起草 / 提示语 / 档案摘要 / 记忆提炼） |
| 可复现 | 生成结果记录 `{persona_id, draft_profile, prompt_version, model}` |
| 降级 | 每个生成点都要有"不调模型也能用"的路径（空白画布、固定兜底文案） |

---

## 5. 账号与合规预留（不上线，但字段先有）

| 项 | 预留 |
|---|---|
| 归属 | 所有新表 `owner_id`；服务签名带 `owner_id`；跨用户访问返回 404（不是 403，避免探测） |
| 未成年人 | `guardian_consent`（布尔，可空）、`age_band`（`unknown` 默认） |
| 用途限定 | `purpose`（`support` / `memory` / `archive` / `export`）写进记录，导出时按用途过滤 |
| 删除权 | 预留 `DELETE /me/data`（501）：全量删除，含密文与墓碑 |
| 可携带权 | 预留 `GET /export/all`（501）：一个 JSON 包，含 schema_version |
| 保留期 | 墓碑 30 天；配置项 `RETENTION_TOMBSTONE_DAYS` |
| 加密 | 内容列加密 + `enc_key_id`；威胁模型写在文档里，不承诺挡住"已控制系统"的攻击者 |

---

## 6. 明确不预留（避免过度设计）

| 不预留 | 理由 |
|---|---|
| `/v1` 版本前缀、API 版本协商 | 全内部客户端；只加不删足够 |
| GraphQL / gRPC | 收益低 |
| 外部消息队列 | 单进程 |
| 向量库 / 嵌入检索 | 条目上千再说；字段 `embedding_ref` 已占位 |
| 多租户 / 团队空间 | 账号都没有 |
| 国际化 | `lang` 字段占位即可 |

---

## 7. 落地顺序

| 时点 | 动作 |
|---|---|
| **画布 M1 建表前** | 1.2 字段、1.4 迁移器、1.5 `enc_key_id`、1.1 的 501 端点与分页参数 |
| M1 同时 | `/health.modules`、`app/events.py`（同步版）、BFF 新路径 |
| M2 | 卡片表、`/draft` 真生成、`/personas`、分页游标真正启用 |
| M3 | 账号、`/export/all`、`DELETE /me/data` |

---

## 附录：现有已预留的盘点

| 已有 | 位置 | 说明 |
|---|---|---|
| `owner_id` | `app/schemas/memory.py`、存储表 | 恒 `local`，索引按 owner 前缀 |
| `POST /memories/propose` | `app/api_memory.py` | 返回 501，等画布确认接入 |
| `schema_version` | 心理、穿戴、理解、状态分析 | 已有版本字段习惯 |
| `builder_version` / `prompt_version` | `ContextManifest` | 上下文与提示词可追溯 |
| `valid_to` / `review_at` / `evidence_count` | 记忆 | 过期与复核预留 |
| `derived_from_record_id` | 记忆 | 与 L1 记录的弱引用 |
| 草稿 `revision` + `expected_revision` | 草稿、存档 | 乐观锁习惯已存在 |
| `snapshot_version` | 存档 | 快照格式版本 |
| 软删 + 墓碑 | 记忆 | 删除语义已存在 |

**缺口**（本清单要补的）：迁移器、`enc_key_id`、`/health.modules`、事件入口、BFF 新路径、分页参数、`/personas`、导出/删除权占位。

---

## 8. 写入规划的状态（v0.2）

| 预留项 | 写进哪了 | 时点 |
|---|---|---|
| 画布字段（schema_version / persona / sensitivity / enc_key_id / merged_from / export_version / embedding_ref / lang） | 《画布设计》§8.1、§8.9 | M1 建表 |
| 画布 501 端点（draft / cards / append / export / personas） | 《画布设计》§5.4、§8.9 | M1 建路由 |
| 分页参数 `?limit=&cursor=` | 《画布设计》§8.9；《长期记忆设计》§12.2、附录 C | M1 |
| `/health.modules` | 《画布设计》§8.9 | M1 |
| `app/events.py` 同步事件入口 | 《画布设计》§8.9、§5.3 | M1 |
| 迁移器（`user_version` + `MIGRATIONS`） | 《画布设计》§8.9；《长期记忆设计》§12.3 | M1 前 |
| `enc_key_id` + 密钥轮换 | 《画布设计》§8.9；《长期记忆设计》§12.1 | M1 |
| BFF `/api/canvas|records|memories/*` | 《画布设计》§8.9 | M1 前端 |
| 记忆预留字段（schema_version / purpose / guardian_consent / embedding_ref / persona_version） | 《长期记忆设计》§12.1 | 记忆加密迁移时一起 |
| 记忆加密改造（当前 v0.1 仍明文） | 《长期记忆设计》§8、§12.3 | 下一步迁移 |
| 账号/合规占位（`DELETE /me/data`、`GET /export/all`） | 本清单 §5 | M3 |

**下一步**：画布 M1 代码按《画布设计》§8.9 建表建路由；记忆加密与预留字段作为独立迁移任务，不塞进画布 PR。
