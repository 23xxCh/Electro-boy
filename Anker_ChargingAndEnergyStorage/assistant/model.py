import json
import re


class Model:
    def __init__(self, api):
        self.api = api

    @staticmethod
    def fact_fields(summary):
        keys=('outlook','battery','supplyDetail','gridDetail')
        if len(summary)>1600:
            raise ValueError('bounded_recall_summary_required')
        return {key:summary[index*400:(index+1)*400] for index,key in enumerate(keys) if summary[index*400:(index+1)*400]}

    def intent(self, text, today, timezone, known_memory=''):
        if len(text) > 180:
            return {'action': 'clarify', 'question': '请把这条消息缩短到180字内，或一次说一件事。'}
        question = (
            '把用户句子识别成JSON且只输出JSON。action=query/status/why/advice/list/memory/delete/plan/clarify。明确请求安排用plan，不能执行。'
            'memory仅用户明确陈述时；字段kind=activity/habit/preference/fact,key,value(字符串),starts_at,expires_at,id(修改时)。'
            'delete带id仅候选；活动日期用带时区ISO；不明clarify带question。普通问句不能记忆。用户句子：' + text)
        # The existing explainer limits questions to 300 characters; keep the whole user sentence.
        if len(question) > 300:
            question = ('输出JSON：action=query/status/why/advice/list/memory/delete/plan/clarify。明确请求安排用plan。memory须明确陈述，'
                        'kind=activity/habit/preference/fact,key,value(字符串),starts_at,expires_at,id；活动用带时区ISO，不明clarify带question。句子：' + text)
        if len(question) > 300:
            return {'action':'clarify','question':'请把这件事再缩短一点，避免理解时丢失你的原话。'}
        answer = self.api.explain(question, {'source': '意图识别；JSON仅候选，不能执行。询问如何按已有资料安排时action=advice，不必重问已有偏好。',
                                            'time': today, 'household': '已绑定家庭；时区=' + (timezone or '未设置'),
                                            **self.fact_fields(known_memory)})
        answer = re.sub(r'^```(?:json)?\s*|\s*```$', '', answer.strip())
        try:
            result = json.loads(answer)
        except ValueError:
            return {'action': 'advice'}
        if not isinstance(result, dict) or result.get('action') not in {'query', 'status', 'why', 'advice', 'list', 'memory', 'delete', 'plan', 'clarify'}:
            return {'action': 'advice'}
        return result

    def advice(self, text, facts):
        templates = {
            'backup_priority':'按你的备电偏好，建议优先遵守已经确认的保底电量，再讨论可延后的用电。',
            'cost_priority':'按你的成本偏好，建议先找出可延后的负荷，再用本机演练预测与电价比较用电窗口。',
            'activity_window':'已把活动和到家需求作为讨论依据；建议先核对活动期间的可运行时段与必须满足的用电需求。',
            'habit_load':'已参考你的日常习惯；建议区分必须按时运行和可以推迟的负荷，避免安排打扰日常生活。',
            'clarify_equipment':'还需明确相关电器的功率、所需电量和允许调整的时段，才能把意愿转为可验证约束。',
        }
        fallback = '本次仅讨论取舍；如需本机演练方案，请说“帮我安排这一时段”，计算后另确认。真实家庭未来输入尚未接入。'
        question = ('根据已有资料选取讨论类别，只输出JSON：{"considerations":[类别]}。类别仅backup_priority、'
                    'cost_priority、activity_window、habit_load、clarify_equipment，最多三项。不输出自由文本、时点、时长或收益。用户问题：' + text)
        if len(question) > 300:
            return '请把这次安排需求缩短一点，避免遗漏你的条件。' + fallback
        answer = self.api.explain(question,facts)
        answer = re.sub(r'^```(?:json)?\s*|\s*```$', '', answer.strip())
        try:
            data = json.loads(answer)
            categories = data['considerations']
            if not isinstance(categories,list) or not 1 <= len(categories) <= 3 or any(
                    not isinstance(item,str) or item not in templates for item in categories):
                return fallback
        except (ValueError,KeyError,TypeError):
            return fallback
        # The model selects qualitative considerations; all visible advice is local copy.
        return '\n'.join(templates[item] for item in dict.fromkeys(categories)) + '\n' + fallback
