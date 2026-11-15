"""Fixed result fixtures test plumbing only, never classify user text."""
from app.schemas.psychology import PsychologyResult


class FixedPsychologyModel:
    def __init__(self, result: PsychologyResult):
        self.result = result.model_copy(deep=True)
        self.calls = 0

    async def generate_structured(self, *, system_prompt, request_json):
        self.calls += 1
        return self.result.model_dump_json()


def fixture_result(route):
    replies = {
        'routine_support': '我们可以先聊聊你现在希望得到怎样的支持；你也可以纠正我的理解。',
        'clarify_safety': '我想先确认你刚才的话指什么，以及你此刻是否需要现实中的帮助。',
        'safety_support': '我们可以先把注意力放在你当下需要的支持，并考虑联系你信任的人。',
        'urgent_real_world_help': '你表达了需要及时现实帮助；请优先寻求身边可信任的人或所在地紧急服务的支持。',
    }
    return PsychologyResult(route=route, evidence_refs=['current_1'], uncertainties=['虚构工程示例，路线与措辞尚待专业审核。'],
        reply=replies[route], suggested_next_step=None, memory_update=None, follow_up_offer=None)
