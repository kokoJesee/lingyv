"""Explicit server-side fixtures for browser integration, never a classifier."""
import json

from app.schemas.psychology import PsychologyRequest
from app.psychology.mock import fixture_result


class ScriptedSupportModel:
    def __init__(self, route='routine_support'):
        self.route = route

    async def generate_structured(self, *, system_prompt, request_json):
        request = PsychologyRequest.model_validate_json(request_json)
        result = fixture_result(self.route)
        result.reply = '【固定脚本演示，不进行心理判断】' + result.reply
        if self.route == 'routine_support':
            confirmed = [r for r in request.records if r.evidence_type == 'confirmed_memory']
            observations = [r for r in request.records if r.evidence_type == 'device_observation']
            result.reply += '\n本轮输入：' + request.current_message.content[:500]
            if confirmed:
                # The input builder prioritizes current understandings, newest
                # update first; legacy confirmed drafts are appended afterwards.
                # Respect that order instead of inferring recency from source IDs.
                result.reply += '\n当前已确认内容：' + confirmed[0].content[:600]
                result.evidence_refs.append(confirmed[0].source_id)
            else:
                for item in observations:
                    info = json.loads(item.content)
                    if not info.get('inquiry_candidate') or info.get('algorithm') != 'lifesnaps_v4':
                        continue
                    if info['metric'] == 'sleep_duration_minutes':
                        minutes = abs(info['delta'])
                        change = '短' if info['delta'] < 0 else '长'
                        question = f'这组回放最近三天的睡眠时长中位数比前两周{change}了 {minutes:g} 分钟。你想聊聊这段时间的感受吗？'
                    elif info['metric'] == 'steps':
                        steps = abs(info['delta'])
                        change = '多' if info['delta'] > 0 else '少'
                        question = f'这组回放最近三天的步数中位数比前两周{change}了 {steps:,g} 步。这几天你想聊些什么？'
                    else:
                        continue
                    asked = any(message.role == 'assistant' and question in message.content for message in request.history)
                    if not asked:
                        result.reply = '【固定脚本演示，不进行心理判断】' + question
                    else:
                        result.reply = '【固定脚本演示，不进行心理判断】谢谢你补充这些。你愿意多说一点发生了什么，还是希望我先听听你现在的感受？'
                    result.evidence_refs.append(item.source_id)
                    break
            result.reply += f'\n本轮收到 {len(observations)} 组已授权设备观察。'
        return result.model_dump_json()
