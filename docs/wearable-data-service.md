# 模块 2：会话内可穿戴数据与用途授权

状态：本地内存服务已实现；无新 HTTP 路由、真实设备调用或模型上下文接入。
服务入口为 `app/services/wearable_service.py`，请求结构在 `app/schemas/wearable_service.py`。
与已有 `SessionRecord` 共用会话锁；身份认证仍由未来调用层实现，知道 session UUID 不代表拥有生产环境访问权。

## 操作及版本

- `import_records(session_id, ImportWearables)`：批次校验后原子写入。结果为 accepted、unchanged、invalid_metrics 和 revision；invalid_metrics 是本次写入的非 valid 指标数，类型非法的批次直接拒绝。
- `set_consent(session_id, SetWearableConsent)`：替换某 provider 的指标及用途选择。`local_analysis` 与 `model_context` 独立，空 scopes 撤回全部用途；不会删除原始数据。
- `delete(session_id, DeleteWearableRecord)`：删除本会话记录。删除过的记录 ID 不可复用，避免旧引用重新生效。
- `snapshot(session_id, scope)`：只返回当前用途有权读取的指标深拷贝；无权限时返回空记录，不泄漏其他会话内容。
- `validate_snapshot(session_id, snapshot)`：下游计算提交前检查版本；任何数据/授权变更均让旧快照失效。采用保守的全局版本，不仅使单项相关结果失效。
- `revision` / `consent`：读取当前设备版本和授权选择，授权对象也是深拷贝。

每次写操作带独立 operation_id 和 expected_revision（设备状态版本，不是 Session.revision）。
同一编号同一内容返回原计数收据，不缓存数据正文；同一编号不同内容拒绝。
新记录 record_revision 必须为 1；修改须递增 1，来源身份不变；同内容重导入不增加版本。
修改检查同时使用全局 CAS 与记录版本，批次中任何冲突均不部分写入。
失败收据不包含设备正文；忙碌、旧全局版本等预检拒绝不登记操作。

## 授权与派生数据

导入仅表示用户向本地会话提供数据，不自动授予任何分析或模型用途。
provider、指标及 scope 必须同时匹配授权；无授权默认不可读取。
派生字段的每个来源引用必须在本会话存在且版本匹配；缺失、过期、循环或无法验证的依赖均不返回该字段。
当前 EvidenceReference 没有指标级引用，因此保守要求被引用记录的全部指标都已获得相同用途授权。
依赖遍历每项最多 64 层、1000 个节点，超限不返回该字段，不猜测权限。

快照是内部数据对象，不是可从客户端信任的授权凭据。后续模块 4/7 必须在同一会话锁下重新检查版本并提交，不能先校验、释放锁后再写入。
本模块没有持久化分析或理解对象；提供的是失效版本接口。通用上下文、历史回复缓存和交互动作的依赖清理在模块 7 接通。

## 生命周期和预算

运行中普通导入/授权扩大返回 session_busy。撤回或删除先设置生命周期屏障，取消当前模型任务，再在锁内完成更新；迟到模型回复沿用现有 Runtime 检查而无法提交。
结束后仍允许删除和缩减已有授权，不允许新增或扩大。清空会话同时删除设备记录和授权、递增版本，并将旧操作变成 request_cleared 墓碑。

操作计数纳入已有会话预算；达到上限仍允许删除/撤回，但不再保存这些操作的重放收据，以免隐私操作被预算阻止或墓碑无限增长。此时重试需要先读取当前版本；旧 expected_revision 仍被拒绝。
每次请求序列化体积及每会话设备记录总量限制为 4 MiB，记录最多 2000 条。服务不接收原始上传文件；将来 HTTP 上传层须在解析前另设文件字节上限。

默认内存，现有存档 v1 不包含设备记录、授权和操作历史；恢复不会恢复设备授权。完整持久化属于模块 8。已发给外部模型的数据无法靠撤回追回；当前尚未将设备数据送入模型。

## 最小复现

在仓库根目录运行：

```bash
.venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_wearable_service.py
```

以下示例可在异步调用层使用（手写一条 steps 记录的方式也受同一契约校验）：

```python
import json
from pathlib import Path
from app.services.session_service import SessionService
from app.services.wearable_service import WearableService
from app.schemas.wearable_service import ImportWearables, SetWearableConsent

async def demo():
    sessions = SessionService()
    sid = sessions.create().session_id
    service = WearableService(sessions)
    batch = json.loads(Path("examples/wearable/batch.json").read_text())
    result = await service.import_records(sid, ImportWearables(expected_revision=0, batch=batch))
    await service.set_consent(sid, SetWearableConsent(
        expected_revision=result["revision"], provider="mock",
        allowed_metrics=["steps"], scopes=["local_analysis"],
    ))
    local = service.snapshot(sid, "local_analysis")
    assert local.records
    assert not service.snapshot(sid, "model_context").records
    service.validate_snapshot(sid, local)
```

测试覆盖导入、冲突、批次回滚、深拷贝、会话隔离、授权隔离、来源依赖、撤回、删除、清空、预算、结束状态和取消后迟到结果。真实模型、设备和生产认证未验证。
