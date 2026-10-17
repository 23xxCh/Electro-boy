from datetime import datetime, timezone, timedelta
import re
import secrets
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .model import Model
from .runtime_tools import RuntimeTools
from .notifications import Notifications


HELP = ('我是家庭储能助手。先用本机提供的一次性码私聊“绑定 <码>”。绑定后可自然询问回放收益、状态和安排，'
        '告诉我活动、习惯与偏好。我会先复述并请你确认。可用：资料、删除 <ID>、忘记全部、时区 <IANA时区>。'
        '可说“家里现在怎么样”“帮我安排这一时段”“我想设置最低留电量”。本机演练方案需明确确认才执行；固定八小时样例单独标识。')
BOUND = '已绑定家庭1。可以直接问“家里现在怎么样”，或告诉我习惯与偏好。当前控制仅限本机仿真；记录活动前需明确家庭时区，例如“时区 Europe/Berlin”。'
BOUND_HELP = ('我是你家的储能助手，可以查状态、记偏好、生成本机演练方案。\n'
              '试试：“家里现在怎么样”“记住我通常18点到家”“我想设置最低留电量”“帮我安排这一时段”。\n'
              '资料先复述，回复“记下 <资料确认号>”才保存；设备另需“执行 <方案确认号>”，只执行首15分钟。\n'
              '可问“我的偏好”查看资料；历史收益单独标注，不承诺节省。')


def normalize_command(text):
    """One normalization boundary for every transport before command checks."""
    text=text.strip().strip('`').strip()
    parts=text.split(None,1)
    if parts and (parts[0] in {'绑定','记下','确认','执行','删除','时区','样例'} or re.fullmatch(r'选\d+',parts[0])):
        return parts[0]+' '+(parts[1].strip().strip('`').strip() if len(parts)>1 else '')
    return text


class Service:
    def __init__(self, store, api, model=None, clock=None, runtime=None):
        self.store, self.api = store, api
        self.memory_v2=store.enable_memory_v2()
        self.model = model or Model(api)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.last_decision_context = None
        self.runtime=runtime or RuntimeTools(store)
        self.notifications=Notifications(store)
        self.store.notifications=self.notifications
        self.last_result=None

    def handle(self, app, sender, message_id, text, request_id=None):
        binding=self.store.binding(app,sender)
        identity={'app':app,'sender':sender,'household':binding['household']} if binding else None
        before=self.notifications.capture_before(identity) if identity else None
        previous_origin=getattr(self.store,'notification_origin',None)
        self.store.notification_origin=message_id
        try:
            reply=self._handle(app,sender,message_id,text)
        finally:
            try:
                if identity:self.notifications.record_changes(identity,before,message_id)
            finally:self.store.notification_origin=previous_origin
        binding=self.store.binding(app,sender)
        if binding:
            identity={'app':app,'sender':sender,'household':binding['household']}
            self.store.interaction(identity,message_id,text,reply,request_id,self.last_result)
        return reply

    def _handle(self, app, sender, message_id, text):
        self.last_result=None
        cached = self.store.receipt(app,message_id,sender)
        if cached and cached.get('action') == 'reply':
            return cached['reply']
        if cached and cached.get('action') == 'binding':
            return BOUND
        if cached and cached.get('action')=='feedback':
            return '已记下反馈 '+cached['feedbackId']+'；修订候选不会自动改变约束。'
        text = normalize_command(text)
        if len(text) > 2000:
            return '请一次说一件事，消息不要超过2000字。'
        if text.startswith('绑定 '):
            valid_code = re.fullmatch(r'[A-Fa-f0-9]{8}',text[3:])
            return BOUND if valid_code and self.store.bind(app, sender, text[3:], message=message_id) else '绑定未成功：码可能错误、过期、已用，或家庭已有owner。请由本机owner重新检查。'
        binding = self.store.binding(app, sender)
        if not binding:
            return HELP
        identity = {'app': app, 'sender': sender, 'household': binding['household']}
        now = self.clock().astimezone(timezone.utc)
        short=text.rstrip('？?。！!').strip()
        if short.lower() in {'你好','您好','嗨','hello','hi','你能做什么','你会做什么','你是谁','帮助','help','/help'}:
            return BOUND_HELP
        if short in {'状态','当前状态','家里现在怎么样','现在怎么样','电池还有多少电','现在电量多少'}:
            reply=self.runtime.live_status(identity);self.last_result=self.runtime.last_result;return reply
        if short in {'资料','我的资料','我的偏好','我的习惯','你记住了什么','记住了什么'}:
            return self.list_memories(identity,now)
        if short in {'执行','确认','记下','执行吧','确认吧','同意','可以执行了'}:
            return '请使用当前回复中的精确确认号：保存资料回复“记下 <资料确认号>”，执行首15分钟回复“执行 <方案确认号>”。未带确认号不会自动选择或执行。'
        waiting=self.store.pending_record(identity)
        if waiting and waiting['data'].get('action')=='clarify' and waiting['data'].get('field')=='minimumPercent' and re.fullmatch(r'\d{1,3}(?:\.\d+)?\s*[%％]',text):
            value=re.search(r'\d{1,3}(?:\.\d+)?',text)[0]
            return self.memory_candidate(identity,{'kind':'preference','key':'软件保底SOC','value':value+'%'},
                                         binding['timezone'],'偏好：电池保底 '+value+'%',now,message_id)
        reserve=re.fullmatch(r'(?:我想|请|帮我)?(?:设置|调整)?\s*(?:软件)?(?:最低留电量|备用电量|保底电量|最低电量|电池保底)(?:为|到|改为|设为)?\s*(\d{1,3}(?:\.\d+)?)\s*[%％]',text.rstrip('。！!'))
        if reserve:
            return self.memory_candidate(identity,{'kind':'preference','key':'软件保底SOC','value':reserve.group(1)+'%'},
                                         binding['timezone'],'偏好：电池保底 '+reserve.group(1)+'%',now,message_id)
        if re.search(r'设置|调整|想',text) and re.search(r'最低留电量|备用电|保底电量|最低电量',text) and not re.search(r'\d\s*[%％]',text):
            self.store.put_pending(identity,{'action':'clarify','field':'minimumPercent','constraintType':'reserve_soc',
                                            'summary':'你希望软件至少保留多少电量？请回复一个百分比；我会先复述，请你确认后再保存。'})
            return '你希望软件至少保留多少电量？请回复一个百分比。我会先复述，请你确认后再保存；不会改动电池硬件保护。'
        runtime_reply=self.runtime.handle(identity,message_id,text)
        if runtime_reply is not None:
            self.last_result=self.runtime.last_result
            return runtime_reply
        if text.startswith('时区 '):
            value = text[3:].strip()
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                return '这个时区无法识别，请提供IANA时区，例如 Europe/Berlin。'
            self.store.timezone(identity, value)
            return '已将家庭活动时区设为 ' + value + '。这不会改历史回放原有的设备时间。'
        reserve = re.fullmatch(r'保底\s+(\d{1,3}(?:\.\d+)?)\s*[%％]',text)
        if reserve:
            return self.memory_candidate(identity,{'kind':'preference','key':'软件保底SOC','value':reserve.group(1)+'%'},
                                         binding['timezone'],'偏好：电池保底 '+reserve.group(1)+'%',now,message_id)
        if text.startswith('删除 '):
            return '已删除该条资料。' if self.store.delete(identity, text[3:].strip(),message=message_id) else '找不到属于你这户的资料ID。'
        if text == '忘记全部':
            token = self.store.put_pending(identity, {'action': 'forget'})
            return f'将删除你在这个家庭保存的全部活动、习惯与偏好。确认请回复“记下 {token}”，十分钟内有效。'
        if re.fullmatch(r'记下 [A-Fa-f0-9]{6}', text):
            token = text[3:].upper()
            pending = cached if cached and cached.get('action') in {'memory','forget'} else self.store.pending(identity, token)
            if not pending or pending['action'] not in {'memory', 'forget'}:
                return '资料确认号不匹配或已过期，请重新告诉我。'
            data = cached if cached and cached.get('action') in {'memory','forget'} else self.store.apply_pending(identity, token, message_id)
            if data and data.get('previewId') and data['action']=='memory':
                try:
                    reply=self.runtime.propose(identity,message_id+':saved',preview_id=data['previewId'])
                    self.last_result=self.runtime.last_result
                    return '已保存这条备用电量偏好；设备尚未动作。\n'+reply
                except (RuntimeError,ValueError,KeyError,TypeError):
                    self.last_result={'kind':'saved_preview_stale','previewId':data['previewId']}
                    return '偏好已保存。原预览已过期、来源已变化或暂不可用，请重新生成方案；设备没有因此动作。'
            return '已清理你的全部个人资料。' if data and data['action'] == 'forget' else ('已保存这条资料。生成计划时会区分可执行约束和讨论依据；当前设备没有因此动作。' if data else '确认失败，请重试。')
        if text.startswith('样例 '):
            kinds = {'冰箱': 'fridge', '车': 'car', '外出': 'trip', '低价': 'cheap'}
            kind = kinds.get(text[3:].strip())
            if not kind:
                return '固定八小时样例支持：样例 冰箱、样例 车、样例 外出、样例 低价。'
            try:
                return self.plan_reply(identity, self.api.plan(identity, 'trial', kind=kind), message_id)
            except RuntimeError:
                return '样例试算接口暂不可用，没有记下或下发任何计划。'
        if re.fullmatch(r'(确认|选\d+) [A-Fa-f0-9]{6}', text):
            command, token = text.split(' ')
            pending = self.store.pending(identity, token.upper())
            if not pending or pending['action'] != 'plan':
                return '方案确认号不匹配或已过期，请重新试算。'
            try:
                if command.startswith('选'):
                    choice = str(int(command[1:]) - 1)
                    if choice not in pending.get('choices', []):
                        return '这个选项不在你看到的方案中。'
                    result = self.api.plan(identity, 'choose', proposalId=pending['proposalId'], choice=choice)
                    return self.plan_reply(identity, result, message_id)
                result = self.api.plan(identity, 'confirm', proposalId=pending['proposalId'])
                reply = '【固定八小时样例】' + result.get('sentence', '方案已记下，尚未下发。')
                self.store.finish_plan(identity,message_id,reply)
                return reply
            except RuntimeError:
                return '方案未确认：当前卡可能已过期或被新方案替代，请重新试算。没有下发设备。'
        if len(text) > 180:
            return '请把这条消息缩短到180字内，或一次说一件事，避免理解时遗漏条件。'
        try:
            local_now = now.astimezone(ZoneInfo(binding['timezone'])) if binding['timezone'] else now
            recalled=self.memory_v2.recall(identity,text,now.isoformat(),(now+timedelta(days=2)).isoformat(),max_chars=1500)
            known='\n'.join(record['summary'] for record in recalled['contextFacts'])
            if recalled['effectiveConstraints']['usedMemoryIds']:
                known='已确认的软件保底电量约束全量保留，数值由真实LP直接消费，不交模型改写。\n'+known
            intent = self.model.intent(text, local_now.isoformat(), binding['timezone'], known)
        except RuntimeError:
            if text=='状态':
                reply=self.runtime.live_status(identity);self.last_result=self.runtime.last_result;return reply
            return '自然语言模型暂不可用。你仍可用“收益”“状态”“资料”查询；资料确认与明确的本机演练入口仍可用。' if text not in {'收益', '为什么'} else self.query(identity, {'收益':'query', '为什么':'why'}[text])
        action = intent.get('action')
        if action in {'query', 'status', 'why'}:
            if action=='status':
                reply=self.runtime.live_status(identity);self.last_result=self.runtime.last_result;return reply
            return self.query(identity, action)
        if action=='plan':
            reply=self.runtime.handle(identity,message_id,'仿真计划');self.last_result=self.runtime.last_result;return reply
        if action == 'list':
            return self.list_memories(identity, now)
        if action == 'clarify':
            return '请再明确一点：你想查询、保存资料，还是讨论安排？若是活动，请告诉我开始、结束时间及家庭时区。'
        if action == 'memory':
            return self.memory_candidate(identity, intent, binding['timezone'], text, now, message_id)
        if action == 'delete':
            record_id = intent.get('id')
            record = next((r for r in self.store.memories(identity) if r['id'] == record_id),None)
            if not record:
                return '请先回复“资料”找到要删除的资料ID，我不会按不确定的猜测删除。'
            return f"你要删除 {record['id']}：{record['key']}＝{record['value']}。确认请明确回复“删除 {record['id']}”。"
        return self.advice(identity, text, now)

    def list_memories(self, identity, now):
        records = self.store.memories(identity)
        if not records:
            return '目前没有保存你的活动、习惯或偏好。'
        lines = ['你在家庭1的资料（删除可回复“删除 ID”）：']
        for item in records:
            expired = item['expires_at'] and item['expires_at'] <= now.isoformat()
            lines.append(f"{item['id']} · {item['kind']} · {item['key']}：{item['value']}" +
                         (f"；有效期 {item['starts_at']} 至 {item['expires_at']}" if item['kind'] == 'activity' else '') +
                         ('（已过期，不用于建议）' if expired else '') +
                         f"；更新于 {datetime.fromtimestamp(item['updated_at'], timezone.utc).isoformat()}；来源：本人确认")
        return '\n'.join(lines)[:6000]

    def memory_candidate(self, identity, data, tz, original, now, source_message_id):
        if '?' in original or '？' in original or not any(word in original for word in ('我', '记', '偏好', '习惯', '改成', '修改')):
            return '这句话可能是提问，我先不当作个人事实。请明确告诉我要记住什么。'
        if data.get('kind') not in {'activity', 'habit', 'preference','fact'}:
            return '请明确这是一次活动、长期习惯，还是偏好。'
        if not all(isinstance(data.get(k), str) and 0 < len(data[k].strip()) <= 160 for k in ('key', 'value')):
            return '这条资料还不够明确，请一次说一件事。'
        candidate = {'action':'memory', 'kind':data['kind'], 'key':data['key'].strip(), 'value':data['value'].strip(),
                     'source_message_id':source_message_id}
        if candidate['value'].lower() in {'true','false'}:
            candidate['value'] = '是' if candidate['value'].lower() == 'true' else '否'
        percentages=re.findall(r'(\d{1,3}(?:\.\d+)?)\s*[%％]',original)
        if data['kind']=='preference' and len(percentages)==1 and re.search(r'电池|电量|储能|SOC',original,re.I) and re.search(r'保底|至少|最低|保留|留',original):
            value=float(percentages[0])
            if not 0<=value<=100:
                return '软件保底电量百分比必须在0到100之间；不会修改设备SOC寄存器。'
            candidate['constraint']={'type':'reserve_soc','value':{'minimumPercent':value}}
            candidate['key'],candidate['value']='软件保底SOC',f'{value:g}%'
            matches=[record for record in self.store.memories(identity) if record['key']=='软件保底SOC' and not record['starts_at'] and not record['expires_at']]
            if not data.get('id') and len(matches)==1:
                candidate['id']=matches[0]['id']
        if data.get('id'):
            if data['id'] not in {r['id'] for r in self.store.memories(identity)}:
                return '修改需提供属于你的资料ID，可先回复“资料”。'
            candidate['id'] = data['id']
        if data['kind'] == 'activity':
            if not tz:
                return '记录这次活动前，请先明确家庭时区，例如“时区 Europe/Berlin”，再告诉我活动。'
            try:
                start, end = (datetime.fromisoformat(data[k]) for k in ('starts_at', 'expires_at'))
                if not start.tzinfo or not end.tzinfo or end <= start or end <= now:
                    raise ValueError()
                candidate['starts_at'], candidate['expires_at'] = (d.astimezone(timezone.utc).isoformat() for d in (start, end))
            except (KeyError, TypeError, ValueError):
                return '请给这次活动的开始和结束时间；我不会猜结束日期或让它永久生效。'
        token = self.store.put_pending(identity, candidate)
        preview_note=''
        if candidate.get('constraint',{}).get('type')=='reserve_soc':
            preview_note=self.runtime.preview_memory(identity,token,source_message_id)
            self.last_result=self.runtime.last_result
        dates = f"；{candidate['starts_at']} 至 {candidate['expires_at']}（UTC，家庭时区 {tz}）" if candidate['kind'] == 'activity' else ''
        machine='；确认后作为软件LP约束，不写设备SOC寄存器' if candidate.get('constraint') else '；目前是个人事实/讨论依据，不能假称已进入LP'
        instruction='请确认这条资料；未确认不会保存。' if source_message_id.startswith('web_') else f'确认请回复“记下 {token}”（十分钟内）；未确认不会保存。'
        label={'preference':'偏好','habit':'习惯','activity':'活动','fact':'个人事实'}[candidate['kind']]
        return f"我理解你的{label}是：{candidate['key']}＝{candidate['value']}{dates}{machine}。\n"+instruction+preview_note

    def query(self, identity, action):
        try:
            payload, site = self.api.comparison(identity['household'])
            scope = payload['scope']
            prefix = f"家庭1 · 本地历史因果回放\n{scope['start']} 至 {scope['endExclusive']}（结束时刻不计入；设备原始时间，时区未知）。\n"
            if action == 'query':
                cash, terminal, total = (self.api.value(site, k) for k in ('cashDeltaEur','terminalCorrectionEur','improvementEur'))
                cash_line = ('基本相同' if round(cash, 2) == 0 else ('少花' if cash > 0 else '多花') + f' €{abs(cash):.2f}')
                total_line = ('基本相同' if round(total, 2) == 0 else ('改善' if total > 0 else '变差') + f' €{abs(total):.2f}')
                return prefix + f'相对原生自用，现金支出{cash_line}；期末电量估值校正 €{terminal:+.2f}；综合{total_line}。\n这是整个比较窗口的实验结果，不是已到账节省或未来收益承诺。'
            slot = site['slots'][-1]
            flow = slot['policy']
            if action == 'why':
                return prefix + f"查看 {slot['start']} 至 {slot['end']}：" + (str(flow.get('reason')) if flow.get('reason') else '源记录没有可核验的决策理由，不能仅凭充放电方向编造动机。')
            return prefix + f"最后已结算区间 {slot['start']} 至 {slot['end']}：\n电池期末SOC {flow['socEndPercent']:.1f}%；光伏 {slot['common']['pvKw']:.2f} kW；负载 {slot['common']['loadKw']:.2f} kW；电池功率 {flow['batteryKw']:+.2f} kW（正数放电）。\n这不是当前真实设备状态。"
        except (RuntimeError, KeyError, TypeError, ValueError):
            return '这户的回放证据暂不可用，当前无法可靠回答状态或收益。没有用旧数据补出数字。'

    def advice(self, identity, text, now):
        recalled=self.memory_v2.recall(identity,text,now.isoformat(),(now+timedelta(days=2)).isoformat(),max_chars=1500)
        used='\n'.join(record['summary'] for record in recalled['contextFacts'])
        selected=set(recalled['usedContextMemoryIds'])
        records=[r for r in self.store.memories(identity) if r['id'] in selected]
        self.last_decision_context = {'usedMemoryIds':recalled['usedContextMemoryIds'],
                                     'omittedMemoryIds':recalled['omittedContextMemoryIds'],
                                     'effectiveConstraints':recalled['effectiveConstraints'],
                                     'constraints':[{'id':r['id'],'kind':r['kind'],'key':r['key'],'value':r['value'],
                                                     'starts_at':r['starts_at'],'expires_at':r['expires_at']} for r in records]}
        hard_note='已确认的软件保底电量约束全量保留供真实LP核验；本次定性讲解不改其值。' if recalled['effectiveConstraints']['usedMemoryIds'] else ''
        context = {'source':'这是非执行的取舍讨论。真正本机演练方案需单独生成并确认；真实家庭输入尚未接入。'+hard_note,
                   'time':now.isoformat(), 'household':'家庭1', **Model.fact_fields(used or '用户尚未保存资料')}
        try:
            answer = self.model.advice(text, context)
        except RuntimeError:
            answer = '模型暂不可用。缺少未来电价、天气和实时家庭状态，暂不能可靠安排真实充放电。'
        omitted=recalled['omittedContextMemoryIds']
        note=f'\n另有{len(omitted)}条上下文资料未召回；已确认硬约束全量结构化保留，不受讲解窗口影响。' if omitted else ''
        return answer + '\n\n本次讲解参考的资料：\n' + (used if used else '暂无。') + note + '\n这段对话仅讨论取舍；真实本机仿真方案需另行生成并明确确认执行。'

    def plan_reply(self, identity, result, message_id):
        proposal = result.get('proposalId')
        if not proposal:
            return '【固定八小时样例】' + result.get('sentence', '未形成待确认方案。')
        token = secrets.token_hex(3).upper()
        lines = ['【固定八小时样例，与当前活动日期无关】', result.get('sentence',''), result.get('line1',''), result.get('line2','')]
        for i, choice in enumerate(result.get('choices', []), 1):
            lines.append(f"{i}. {choice['label']}：{choice.get('line1','')}")
        lines.append(f'回复“选1 {token}”选择样例选项。' if result.get('choices') else f'回复“确认 {token}”仅记下样例；尚未下发。')
        lines.append('确认号十分钟有效；此样例未使用你的活动或偏好，也不是实时家庭未来安排。')
        reply = '\n'.join(filter(None, lines))
        self.store.put_pending(identity, {'action':'plan', 'proposalId':proposal,
                                         'choices':[str(c['id']) for c in result.get('choices', [])],
                                         'choiceDetails':result.get('choices',[]),'summary':reply},
                               token=token,message=message_id,reply=reply)
        return reply
