"""Shared assistant tools for explicitly confirmed local-simulator decisions."""
import math
import re
import time
import uuid
import json

from .runtime_api import RuntimeAPI,RuntimeOutcomeUnknown,RuntimeUnavailable


class RuntimeTools:
    def __init__(self, store, api=None):
        self.store=store
        self.memory=store.enable_memory_v2()
        self.api=api or RuntimeAPI()
        self.last_result=None

    def handle(self, identity, message_id, text):
        self.last_result=None
        if text.rstrip('？?。！!') in {'环境机状态','查看环境机状态'}:
            return self.environment_status(identity)
        try:
            if text.rstrip('？?。！!') in {'解释这次安排','这项留电要求改变了什么','这份安排改变了什么'}:
                return self.explain_selected(identity)
            if text.rstrip('？?。！!') in {'仿真计划','生成仿真计划','帮我安排这一时段','帮我安排用电','生成计划','帮我安排一下','帮我安排充放电'}:
                return self.propose(identity,message_id)
            match=re.fullmatch(r'执行\s+([A-Fa-f0-9]{6})',text)
            if match:
                return self.confirm(identity,message_id,match.group(1).upper())
            if text=='决策记录':
                rows=self.store.db.execute('SELECT id,state,created_at FROM episodes WHERE app=? AND sender=? AND household=? ORDER BY created_at DESC LIMIT 20',self.store.scope(identity)).fetchall()
                return '\n'.join(f"{row['id']} · {row['state']}" for row in rows) or '还没有本机仿真决策记录。'
            if text=='仿真进度' or text.startswith('仿真进度 '):
                episode_id=text[5:].strip()
                if not episode_id:
                    latest=self.store.db.execute('SELECT id FROM episodes WHERE app=? AND sender=? AND household=? ORDER BY created_at DESC LIMIT 1',self.store.scope(identity)).fetchone()
                    episode_id=latest[0] if latest else ''
                if not self.memory.episode(identity,episode_id):
                    return '找不到属于你这户的episode。'
                receipt=self.api.reconcile(identity,episode_id)
                self.sync(identity,receipt)
                return self.receipt_text(identity,episode_id)
            if text.startswith('反馈 '):
                parts=text.split(None,2)
                if len(parts)!=3 or not self.memory.episode(identity,parts[1]) or not 0<len(parts[2])<=400:
                    return '请用“反馈 <本人的episodeId> <合适/不合适及原因>”，最多400字。'
                token=self.store.put_pending(identity,{'action':'feedback','episodeId':parts[1],
                                                       'summary':parts[2],'source_message_id':message_id})
                self.last_result={'kind':'feedback_candidate','episodeId':parts[1]}
                return f'我理解你的反馈是：{parts[2]}。确认请回复“记下 {token}”；不会自动改变保底电量、安全边界或算法。'
            if re.fullmatch(r'记下 [A-Fa-f0-9]{6}',text):
                pending=self.store.pending(identity,text[3:].upper())
                if pending and pending.get('action')=='feedback':
                    feedback_id=self.store.confirm_feedback(identity,text[3:].upper(),message_id)
                    self.last_result={'kind':'feedback_saved','feedbackId':feedback_id,'episodeId':pending['episodeId']}
                    return self.feedback_revision(identity,feedback_id,message_id)
            if text.startswith('修订反馈 '):
                parts=text.split(None,2)
                if len(parts)!=3:
                    return '请明确反馈ID和希望修改的保底百分比。'
                return self.feedback_revision(identity,parts[1],message_id,parts[2])
            if text.startswith('删除反馈 '):
                return '已删除反馈并撤回相关修订候选。' if self.memory.delete_feedback(identity,text[5:].strip()) else '找不到属于你的反馈。'
        except RuntimeOutcomeUnknown:
            return '执行结果未知。没有重发控制，请使用“仿真进度 <episodeId>”核对已有回执。'
        except RuntimeUnavailable as error:
            self.last_result={'kind':'runtime_blocked','reasonCode':str(error)}
            return '本机仿真工具暂不可用或条件未满足；没有用固定样例冒充真实计划。原因代码：'+str(error)[:160]
        except (ValueError,KeyError,TypeError):
            self.last_result={'kind':'runtime_rejected','reasonCode':'invalid_runtime_proof'}
            return '本机仿真方案或技术回执未通过核验，当前不能执行或声称已完成。'
        return None

    def environment_status(self, identity):
        """Explicit remote-source query; never redirects the local status/plan tools."""
        try:
            self.api.owner(identity)
            snapshot=self.api.environment_snapshot()
            self.last_result={'kind':'environment_status','environment':snapshot['environment'],'status':snapshot['status']}
            if snapshot['status']=='disabled':
                return '环境机管线尚未启用，也未做连通验证。当前本机仿真状态与环境机分开；没有向环境机下发控制。'
            if snapshot['status']=='unavailable':
                return '环境机状态暂不可用，尚未完成环境身份与连通验收；没有用本机或历史数据替代，也没有下发控制。'
            observed=snapshot['observation']
            values=[]
            for key,label,unit in (('socPercent','电量','%'),('pvKw','光伏',' kW'),('loadKw','负荷',' kW'),('batteryActualKw','电池实际功率',' kW')):
                value=observed.get(key)
                values.append(label+('暂缺' if value is None else f'{value:g}{unit}'))
            return '环境机官方 HA 读数（家庭1，环境身份／时间基准未验收）：'+ '，'.join(values)+'。设备时刻：'+(snapshot.get('deviceTime') or '暂缺')+'。这只是读数，远端控制仍未放行；没有结算或节省金额。'
        except (RuntimeUnavailable,AttributeError,ValueError,KeyError,TypeError):
            self.last_result={'kind':'environment_status','environment':'release_official_ha_unverified','status':'unavailable'}
            return '环境机管线候选暂不可用或尚未部署；未做本轮连通测试，没有改用本机数据或下发控制。'

    def explain_selected(self, identity):
        selected=self.store.db.execute('SELECT * FROM decision_selection WHERE app=? AND sender=? AND household=?',self.store.scope(identity)).fetchone()
        self.last_result={'kind':'decision_explanation'}
        if not selected or not (selected['episode_id'] or selected['preview_id']):
            return '还没有当前安排。先告诉我最低想保留多少电量，或请我生成这一时段的方案。'
        from .decision_view import safe_view
        view=safe_view(self.api.decision(identity,episode_id=selected['episode_id'],preview_id=None if selected['episode_id'] else selected['preview_id']))
        self.last_result.update(episodeId=view.get('episodeId'),previewId=view.get('previewId'))
        def number(value):
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('explanation_numeric_missing')
            return value
        def action(plan):
            first=plan.get('firstAction')
            if not first:return '暂无可执行动作'
            power=number(first['targetPowerW'])
            return '待机' if power==0 else ('充电' if power<0 else '放电')+f'{abs(power):g} W'
        origin=view.get('origin') or {};plan=view.get('plan') or {};preview=view.get('preview')
        hours=number(origin['dtHours'])*len(plan.get('points',[]))
        lines=['这份安排来自同一组冻结输入；以下是计划取舍，不是节省承诺。']
        if view['availability']=='stale' or selected['memory_version']!=self.memory.version(identity) and view['execution']['phase'] not in {'settled','outcome'}:
            lines.append('原比较或资料版本已过期，只能作为旧计算查看，需重新生成方案后才能执行。')
        if preview and preview.get('sameOrigin') and preview.get('sourceSnapshotDigest')==origin.get('sourceSnapshotDigest') and preview.get('forecastDigest')==origin.get('forecastDigest'):
            base,candidate=preview['baseline'],preview['candidate']
            lines.append(f"LP最低留电量由{number(base['reserveEnergyKwh']):g} kWh变为{number(candidate['reserveEnergyKwh']):g} kWh；首15分钟由{action(base)}变为{action(candidate)}。")
            if candidate['status']=='infeasible':
                lines.append('这项要求在当时输入下不可行，没有可执行方案。')
            else:
                change=number(candidate['expectedHorizonCostEur'])-number(base['expectedHorizonCostEur'])
                direction='基本不变' if abs(change)<.005 else ('增加' if change>0 else '减少')+f' €{abs(change):.2f}'
                lines.append(f'同一预计窗口（{hours:g}小时）的净费用{direction}；这是两份计划的比较，不是实际收益。')
        elif plan:
            lines.append(f"这份LP计划的最低留电量为{number(plan['reserveEnergyKwh']):g} kWh，首15分钟{action(plan)}。没有同源调整前对照，不能判断改变来自哪项要求。")
        actual=view.get('actual')
        if actual and actual.get('readbackMatches') is True and actual.get('settlementAvailable') is True:
            lines.append(f"已核验的这15分钟：电量{number(actual['socStartPercent']):g}%→{number(actual['socEndPercent']):g}%，实际净电费 €{number(actual['intervalCostEur']):.4f}。回读与结算证据已齐；这不是节省金额，不能与整个预计窗口直接相减。")
        else:
            lines.append('这份安排还没有完整的实际回读与结算，预计曲线不能当作已经发生。')
        return '\n'.join(lines)

    def home(self, identity):
        snapshot=self.api.snapshot()
        telemetry=snapshot['ha']['telemetry'][identity['household']]
        controls=snapshot['ha']['controls'][identity['household']]
        def number(value,scale=1):
            return value/scale if not isinstance(value,bool) and isinstance(value,(int,float)) and math.isfinite(value) else None
        from .decision_view import observation
        observed=observation(snapshot)
        return {'environment':snapshot['environment'],'deviceTime':snapshot['sourceTime'],'timeBasis':snapshot['targetWindow']['timeBasis'],
                'observedAt':snapshot.get('capturedAt'),'paused':snapshot.get('paused'),'socPercent':number(telemetry.get('soc_pct')),
                'batteryPowerKw':number(controls.get('battery_power_w'),1000),'pvKw':number(telemetry.get('solar_power_w'),1000),
                'loadKw':number(telemetry.get('home_load_w'),1000),'physicalInputsKind':snapshot.get('physicalInputsKind'),
                'measurementSourceSlot':snapshot.get('telemetryPhysicalAsOfSlot'),'socBoundarySlot':snapshot.get('socBoundarySlot'),
                'controlSourceSlot':snapshot['sourceSlot'],'batteryActualKw':observed['batteryActualKw'],
                'batteryTargetKw':observed['batteryTargetKw'],'gridImportKw':observed['gridImportKw'],
                'gridExportKw':observed['gridExportKw'],'prices':observed['prices']}

    def live_status(self, identity):
        try:
            home=self.home(identity)
            self.last_result={'kind':'device_status','home':home}
            soc=f"电池还有{home['socPercent']:g}%电量" if home['socPercent'] is not None else '暂时读不到电池电量'
            power=home['batteryPowerKw']
            state='设备状态暂不可用' if power is None else '设备待机' if power==0 else '回读显示电池充电' if power<0 else '回读显示电池放电'
            paused='本机演练已暂停。' if home['paused'] is True else '本机演练正在运行。' if home['paused'] is False else '本机演练的暂停状态暂不可用。'
            return f"最新设备回读：{soc}，{state}。{paused}"
        except (RuntimeUnavailable,ValueError,KeyError,TypeError):
            self.last_result={'kind':'device_unavailable'}
            return '最新本机设备回读暂不可用。我不会把历史回放当作当前状态；历史收益可另行查询。'

    def feedback_revision(self, identity, feedback_id, message_id, explicit=None):
        feedback=next((row for row in self.memory.feedback_records(identity) if row['id']==feedback_id and row['state']=='recorded'),None)
        if not feedback:
            return '找不到属于你的有效反馈。'
        text=explicit if explicit is not None else feedback['summary']
        percentages=re.findall(r'(\d{1,3}(?:\.\d+)?)\s*[%％]',text)
        current=next((row for row in self.store.memories(identity) if row['key']=='软件保底SOC'),None)
        memory_id=current['id'] if current else None
        if len(percentages)!=1 or not re.search(r'保底|最低|至少|保留',text):
            if '不合适' in text or '希望' in text or '改' in text:
                candidate=self.memory.revision_candidate(identity,memory_id,feedback_id,{},message_id)
                self.last_result={'kind':'revision_needs_clarification','feedbackId':feedback_id,'revisionCandidateId':candidate}
                return f'已记下反馈 {feedback_id}。修订候选 {candidate} 尚缺你希望改变的具体内容，不能确认或改硬约束。可说“修订反馈 {feedback_id} 保底<你的百分比>%”。'
            return f'已记下反馈 {feedback_id}，没有据此自动产生永久习惯或改变约束。'
        percent=float(percentages[0])
        if not 0<=percent<=100:
            return f'反馈 {feedback_id} 已保留；保底百分比需在0到100之间，修订尚未生效。'
        proposed={'kind':'preference','key':'软件保底SOC','value':f'{percent:g}%',
                  'constraint':{'type':'reserve_soc','value':{'minimumPercent':percent}}}
        candidate=self.memory.revision_candidate(identity,memory_id,feedback_id,proposed,message_id)
        data={'action':'memory',**proposed,'revisionCandidateId':candidate,'feedbackId':feedback_id,
              'source_message_id':message_id}
        if memory_id:
            data['id']=memory_id
        token=self.store.put_pending(identity,data)
        self.last_result={'kind':'revision_candidate','feedbackId':feedback_id,'revisionCandidateId':candidate}
        return f'反馈 {feedback_id} 已保留。根据你明确的新意愿，候选将软件保底电量改为{percent:g}%；不写设备SOC寄存器。确认请回复“记下 {token}”。未确认不会改约束。'

    def preview_memory(self, identity, token, message_id):
        self.last_result=None
        if not hasattr(self.api,'preview'):return ''
        data=self.store.pending(identity,token)
        try:
            snapshot=self.api.snapshot();window=snapshot['targetWindow']
            effective=self.memory.effective_constraints(identity,window['start'],window['end'],window['timeBasis'])
            request=str(uuid.uuid5(uuid.NAMESPACE_URL,identity['app']+':preview:'+message_id))
            view=self.api.preview(identity,effective,data['constraint']['value']['minimumPercent'],data.get('id'),request)
            if not self.store.pending(identity,token):return ''
            data.update(previewId=view['previewId'],candidateDigest=view['preview']['candidateDigest'])
            self.store.put_pending(identity,data,token=token)
            self.store.select_decision(identity,preview_id=view['previewId'],candidate_digest=data['candidateDigest'])
            self.last_result={'kind':'memory_preview','previewId':view['previewId']}
            return '\n预计对比已更新；尚未保存或执行。' if view['preview']['status']!='infeasible' else '\n当前来源下这项要求暂不可行，数据台已标出；未下发设备。'
        except (RuntimeError,ValueError,KeyError,TypeError):
            self.last_result={'kind':'preview_unavailable'}
            return '\n预计对比暂不可用；资料仍需确认，设备没有动作。'

    def propose(self, identity, message_id, preview_id=None):
        snapshot=self.api.snapshot()
        window=snapshot['targetWindow']
        effective=self.memory.effective_constraints(identity,window['start'],window['end'],window['timeBasis'])
        request=str(uuid.uuid5(uuid.NAMESPACE_URL,identity['app']+':proposal:'+message_id))
        proposal=self.api.proposal(identity,effective,request,preview_id) if preview_id else self.api.proposal(identity,effective,request)
        execution=proposal['execution']
        power=execution['targetPowerW']
        if (isinstance(power,bool) or not isinstance(power,(int,float)) or not math.isfinite(power) or
                execution.get('intervalMinutes')!=15 or execution.get('intervalCount')!=1 or
                execution.get('signConvention')!='negative_charge_positive_discharge' or
                execution.get('sourceSlot')!=proposal['sourceSlot'] or proposal['sourceSlot']!=snapshot['sourceSlot'] or
                proposal.get('constraintsVersion')!=effective['constraintsVersion']):
            raise ValueError('execution_card_invalid')
        episode_id=proposal['episodeId']
        self.store.select_decision(identity,episode_id=episode_id,preview_id=preview_id)
        if not self.memory.episode(identity,episode_id):
            self.memory.create_episode(identity,effective,episode_id)
            self.memory.episode_event(identity,episode_id,'proposed',{
                'proposalId':proposal['proposalId'],'consumedMemoryIds':proposal['consumedMemoryIds'],
                'unsupportedMemoryIds':[r['memoryId'] for r in proposal.get('unsupported',[])],
                'rejectedMemoryIds':proposal.get('rejectedMemoryIds',[]),'sourceSlot':proposal['sourceSlot'],
                'constraintsVersion':effective['constraintsVersion'],'environment':'local_debug1_sim'})
        action='充电' if power<0 else '放电' if power>0 else '待机'
        summary=f"建议接下来的15分钟{action} {abs(power):g} W。这是本机演练，只执行这一时段，确认前设备不会动作。"
        token=self.store.put_pending(identity,{'action':'runtime_plan','episodeId':episode_id,'proposalId':proposal['proposalId'],
                    'expiresAt':proposal['expiresAt'],'targetWindow':window,'constraintsVersion':effective['constraintsVersion'],
                    'memoryVersion':effective['memoryVersion'],'usedMemoryIds':effective['usedMemoryIds'],
                    'consumedMemoryIds':proposal['consumedMemoryIds'],'summary':summary,'execution':execution})
        self.last_result={'kind':'simulation_plan','episodeId':episode_id,'proposalId':proposal['proposalId']}
        consumed='已纳入你确认的软件保底电量。' if proposal['consumedMemoryIds'] else '到家习惯仍是讨论依据，没有据此猜出设备负荷。'
        instruction='请确认下面的方案。' if message_id.startswith('web_') else f'确认请回复“执行 {token}”。'
        return summary+'\n'+consumed+'\n'+instruction+'只操作本机演练；后续时段不会自动执行，不保证节省金额。'

    def confirm(self, identity, message_id, token):
        pending=self.store.pending(identity,token)
        if not pending or pending['action']!='runtime_plan' or time.time()>pending['expiresAt']:
            return '仿真方案确认号不匹配或已过期，请重新生成。'
        episode_id=pending['episodeId']
        episode=self.memory.episode(identity,episode_id)
        if not episode:
            raise ValueError('episode_missing')
        if episode['state'] in {'confirmed','dispatched','unknown','acknowledged','settled','outcome'}:
            self.sync(identity,self.api.reconcile(identity,episode_id))
            return self.receipt_text(identity,episode_id)
        window=pending['targetWindow']
        current=self.memory.effective_constraints(identity,window['start'],window['end'],window['timeBasis'])
        if current['constraintsVersion']!=pending['constraintsVersion'] or current['memoryVersion']!=pending['memoryVersion']:
            self.last_result={'kind':'runtime_rejected','reasonCode':'memory_changed','episodeId':episode_id}
            self.memory.episode_event(identity,episode_id,'rejected',{'reasonCode':'memory_changed'})
            reply='资料或有效约束已变化，这份旧方案不能执行。请重新生成计划。'
            self.store.finish_plan(identity,message_id,reply)
            return reply
        confirmation=str(uuid.uuid5(uuid.NAMESPACE_URL,identity['app']+':confirm:'+message_id))
        self.memory.episode_event(identity,episode_id,'confirmed',{'proposalId':pending['proposalId'],'confirmationId':confirmation,'sourceMessageId':message_id})
        try:
            receipt=self.api.confirm(identity,episode_id,pending['proposalId'],confirmation)
        except RuntimeOutcomeUnknown:
            self.memory.episode_event(identity,episode_id,'unknown',{'requestId':episode_id,'reasonCode':'transport_outcome_unknown','environment':'local_debug1_sim'})
            self.last_result={'kind':'execution_unknown','episodeId':episode_id}
            return f'本机演练执行结果尚不明确，未重发。请查“仿真进度 {episode_id}”只读核对原请求，不能把超时当作未执行。'
        except RuntimeUnavailable:
            self.memory.episode_event(identity,episode_id,'rejected',{'reasonCode':'core_confirmation_rejected'})
            reply='核心拒绝这份仿真确认，可能状态或来源槽已变化。没有把拒绝当作已执行，请重新生成计划。'
            self.store.finish_plan(identity,message_id,reply)
            self.last_result={'kind':'runtime_rejected','episodeId':episode_id}
            return reply
        self.sync(identity,receipt)
        reply=self.receipt_text(identity,episode_id)
        self.store.finish_plan(identity,message_id,reply)
        return reply

    def sync(self, identity, receipt):
        if receipt.get('environment')!='local_debug1_sim' or receipt.get('owner')!=self.api.owner(identity):
            raise ValueError('runtime_owner_or_environment_mismatch')
        episode_id=receipt['episodeId']
        episode=self.memory.episode(identity,episode_id)
        if not episode or episode['proposal_id']!=receipt.get('proposalId'):
            raise ValueError('runtime_episode_binding_mismatch')
        stages=[row['stage'] for row in receipt.get('events',[])]
        dispatch=receipt.get('dispatch',{})
        proof=receipt.get('evidence',{})
        if 'dispatched' in stages and not any(event['phase']=='dispatched' for event in episode['events']):
            if dispatch.get('requestId')!=episode_id:
                raise ValueError('dispatch_request_mismatch')
            self.memory.episode_event(identity,episode_id,'dispatched',{'requestId':episode_id,'environment':'local_debug1_sim',
                                      'sourceSlot':dispatch['sourceSlot'],'targetKw':dispatch['targetPowerW']/1000})
        episode=self.memory.episode(identity,episode_id)
        if 'acknowledged' in stages and not any(event['phase']=='acknowledged' for event in episode['events']):
            if (proof.get('requestId')!=episode_id or proof.get('sourceSlot')!=receipt['sourceSlot'] or
                    proof.get('nextSlot')!=receipt['sourceSlot']+1 or proof.get('readbackMatches') is not True):
                raise ValueError('readback_proof_mismatch')
            self.memory.episode_event(identity,episode_id,'acknowledged',{'requestId':episode_id,'evidenceKind':'ha_readback',
                                      'readbackMatches':True,'sourceSlot':proof['nextSlot']})
        episode=self.memory.episode(identity,episode_id)
        if 'settled' in stages and not any(event['phase']=='settled' for event in episode['events']):
            self.memory.episode_event(identity,episode_id,'settled',{'requestId':episode_id,'evidenceKind':proof.get('evidenceKind'),
                                      'settlementAvailable':proof.get('settlementAvailable'),'costEur':proof.get('intervalCostEur'),
                                      'sourceSlot':proof.get('nextSlot')})
        episode=self.memory.episode(identity,episode_id)
        if 'outcome' in stages and episode['state']!='outcome':
            self.memory.episode_event(identity,episode_id,'outcome',{'settlementAvailable':receipt.get('outcome',{}).get('settlementAvailable'),
                                      'success':True,'sourceSlot':proof.get('nextSlot')})
        elif receipt.get('status')=='unknown' and episode['state']!='unknown':
            self.memory.episode_event(identity,episode_id,'unknown',{'requestId':episode_id,'reasonCode':'core_execution_unknown','environment':'local_debug1_sim'})
        self.last_result={'kind':'execution_receipt','episodeId':episode_id,'stage':self.memory.episode(identity,episode_id)['state']}

    def receipt_text(self, identity, episode_id):
        episode=self.memory.episode(identity,episode_id)
        if episode['state']=='outcome':
            settlement=next(event['evidence'] for event in episode['events'] if event['phase']=='settled')
            detail=''
            if hasattr(self.api,'decision'):
                try:
                    from .decision_view import safe_view
                    view=safe_view(self.api.decision(identity,episode_id=episode_id))
                    actual=view.get('actual') or {}
                    dispatched=next(event['evidence'] for event in episode['events'] if event['phase']=='dispatched')
                    values=[actual.get(key) for key in ('batteryActualKw','socStartPercent','socEndPercent')]
                    if (view.get('episodeId')==episode_id and actual.get('readbackMatches') is True and
                            actual.get('settlementAvailable') is True and
                            actual.get('executedSlot')==dispatched['sourceSlot'] and
                            actual.get('observedBoundarySlot')==dispatched['sourceSlot']+1 and
                            all(isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) for value in values)):
                        power,start,end=values
                        if 0<=start<=100 and 0<=end<=100:
                            action='待机' if power==0 else '充电' if power<0 else '放电'
                            detail=f'实际电池{action} {abs(power)*1000:g} W，SOC {start:.2f}%→{end:.2f}%。'
                except (RuntimeError,ValueError,KeyError,TypeError,StopIteration):
                    pass
            return f"这次本机演练已执行、读回核验并结算。{detail}本时段净电费 €{settlement['costEur']:.4f}（不是节省金额）；后续时段尚未执行。"
        return f'这次本机演练还没有完整结算证据；未知写入不会自动重发。可用“仿真进度 {episode_id}”只读核对原请求。'
