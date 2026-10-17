import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from assistant.energy_api import EnergyAPI
from assistant.service import Service
from assistant.store import Store
from channels.feishu import Worker, normalize


NOW = datetime(2026,10,17,0,0,tzinfo=timezone.utc)


class FakeModel:
    result = {'action':'advice'}
    def intent(self,*args): return copy.deepcopy(self.result)
    def advice(self,text,facts): self.facts = facts; return '建议先考虑你的备电需要，再决定是否安排可延后的负荷。'


class FakeAPI:
    def __init__(self): self.calls = []; self.current = 'p1'
    def plan(self,identity,action,**kwargs):
        self.calls.append((identity,action,kwargs))
        if action == 'confirm' and kwargs.get('proposalId') != self.current:
            raise RuntimeError('proposal_mismatch')
        return {'proposalId':self.current,'sentence':'方案已记下，尚未下发。','choices':[]}
    def comparison(self, household): raise RuntimeError('offline')


class AssistantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'db.sqlite'
        self.store = Store(self.path)
        self.model, self.api = FakeModel(),FakeAPI()
        self.service = Service(self.store,self.api,self.model,lambda:NOW)
        self.identity = {'app':'app','sender':'ou_owner','household':'site_1'}
        self.code = self.store.pair_code('app')
        self.store.bind('app','ou_owner',self.code)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def save(self,**data):
        base = {'action':'memory','kind':'preference','key':'优先级','value':'备电优先'}
        base.update(data)
        token = self.store.put_pending(self.identity,base)
        return self.store.apply_pending(self.identity,token,'source_'+token)

    def test_unbound_and_pairing(self):
        reply = self.service.handle('app','ou_other','m1','收益')
        self.assertIn('先用',reply)
        self.assertFalse(self.store.bind('app','ou_other',self.code))
        code = self.store.pair_code('second',now=0)
        self.assertFalse(self.store.bind('second','ou_other',code,now=601))
        self.assertFalse(self.store.bind('second','ou_other','WRONG',now=0))

    def test_memory_requires_confirmation_and_is_scoped(self):
        self.model.result = {'action':'memory','kind':'habit','key':'到家','value':'通常18点'}
        reply = self.service.handle('app','ou_owner','m1','记住我通常18点到家')
        self.assertFalse(self.store.memories(self.identity))
        token = reply.split('记下 ')[1][:6]
        self.service.handle('app','ou_owner','m2','记下 '+token)
        self.assertEqual(len(self.store.memories(self.identity)),1)
        for scope in ({**self.identity,'app':'other'}, {**self.identity,'sender':'ou_other'}, {**self.identity,'household':'site_2'}):
            self.assertFalse(self.store.memories(scope))
            self.assertIsNone(self.store.pending(scope,token))

    def test_pending_replacement_expiry_and_receipt(self):
        data = {'action':'memory','kind':'habit','key':'到家','value':'18点'}
        old = self.store.put_pending(self.identity,data,now=0)
        new = self.store.put_pending(self.identity,data,now=1)
        self.assertIsNone(self.store.pending(self.identity,old,now=2))
        self.assertIsNone(self.store.pending(self.identity,new,now=602))
        first = self.store.apply_pending(self.identity,new,'om_once',now=2)
        second = self.store.apply_pending(self.identity,new,'om_once',now=3)
        self.assertEqual(first['id'],second['id'])
        self.assertNotIn('value',second)
        self.assertEqual(len(self.store.memories(self.identity)),1)

    def test_upcoming_expired_update_delete_used_constraints(self):
        future = self.save(kind='activity',key='外出',value='明天外出',starts_at='2026-10-18T00:00:00+00:00',expires_at='2026-10-19T00:00:00+00:00')
        expired = self.save(kind='activity',key='旧活动',value='旧',starts_at='2026-10-15T00:00:00+00:00',expires_at='2026-10-16T00:00:00+00:00')
        pref = self.save()
        self.service.advice(self.identity,'建议',NOW)
        self.assertIn(future['id'],self.service.last_decision_context['usedMemoryIds'])
        self.assertNotIn(expired['id'],self.service.last_decision_context['usedMemoryIds'])
        self.save(id=pref['id'],value='省钱优先')
        self.service.advice(self.identity,'建议',NOW)
        self.assertTrue(any(r['value']=='省钱优先' for r in self.service.last_decision_context['constraints']))
        self.store.delete(self.identity,pref['id'])
        self.service.advice(self.identity,'建议',NOW)
        self.assertNotIn(pref['id'],self.service.last_decision_context['usedMemoryIds'])

    def test_activity_needs_timezone_and_end(self):
        self.model.result = {'action':'memory','kind':'activity','key':'在家','value':'明天在家'}
        self.assertIn('时区',self.service.handle('app','ou_owner','m1','记住我明天在家'))
        self.service.handle('app','ou_owner','m2','时区 Asia/Hong_Kong')
        self.assertIn('开始和结束',self.service.handle('app','ou_owner','m3','记住我明天在家'))

    def test_old_proposal_rejects_and_no_implicit_confirm(self):
        reply = self.service.handle('app','ou_owner','m1','样例 冰箱')
        token = reply.split('确认 ')[1][:6]
        self.model.result = {'action':'confirm','proposalId':'p1'}
        self.service.handle('app','ou_owner','m2','批准吧')
        self.assertEqual(len(self.api.calls),1)
        self.api.current = 'p2'
        self.assertIn('未确认',self.service.handle('app','ou_owner','m3','确认 '+token))
        self.assertIn('样例',reply)

    def test_model_and_energy_unavailable(self):
        def fail(*args): raise RuntimeError('unavailable')
        self.model.intent = fail
        self.assertIn('模型暂不可用',self.service.handle('app','ou_owner','m1','聊聊吧'))
        self.assertIn('证据暂不可用',self.service.handle('app','ou_owner','m2','收益'))

    def test_model_clarification_is_not_free_text_plan(self):
        self.model.result={'action':'clarify','question':'明天九点充电能省三欧。'}
        reply=self.service.handle('app','ou_owner','m1','有什么建议？')
        self.assertNotIn('九点',reply)
        self.assertNotIn('三欧',reply)

    def test_duplicate_restart_unknown_no_resend(self):
        sends=[]
        worker=Worker('app',self.store,self.service,lambda *args:sends.append(args) or False)
        event={'sender':'ou_owner','message':'om_1','text':'帮助'}
        worker.receive(event);worker.receive(event)
        self.assertEqual(len(sends),1)
        reopened=Store(self.path)
        Worker('app',reopened,self.service,lambda *args:sends.append(args)).recover()
        self.assertEqual(len(sends),1)
        self.assertEqual(reopened.counts()['unknown'],1)
        self.assertEqual(reopened.db.execute('SELECT count(*) FROM work').fetchone()[0],0)
        reopened.db.close()

    def test_recover_received_and_computed(self):
        self.store.claim('app','om_recover','uuid','ou_owner','帮助')
        self.store.claim('app','om_computed','uuid2','ou_owner','帮助')
        self.store.save_reply('app','om_computed','已计算答案')
        sends=[]
        Worker('app',self.store,self.service,lambda *args:sends.append(args) or True).recover()
        self.assertEqual(len(sends),2)
        self.assertIn('已计算答案',[s[1] for s in sends])

    def test_binding_commit_before_reply_recovers_same_result(self):
        code=self.store.pair_code('crash')
        first=self.service.handle('crash','ou_crash','om_bind','绑定 '+code)
        self.assertIn('已绑定',first)
        self.assertEqual(first,self.service.handle('crash','ou_crash','om_bind','绑定 '+code))
        self.assertIn('未成功',self.service.handle('crash','ou_crash','om_new','绑定 '+code))
        self.assertIn('未成功',self.service.handle('crash','ou_other','om_bind','绑定 '+code))

    def test_memory_commit_before_reply_recovers_receipt(self):
        token=self.store.put_pending(self.identity,{'action':'memory','kind':'habit','key':'到家','value':'18点'})
        self.store.claim('app','om_memory','uuid','ou_owner','记下 '+token)
        self.store.state('app','om_memory','processing')
        first=self.service.handle('app','ou_owner','om_memory','记下 '+token)
        self.assertIsNone(self.store.pending(self.identity,token))
        sent=[]
        Worker('app',self.store,self.service,lambda *args:sent.append(args) or True).recover()
        self.assertEqual(sent[0][1],first)
        self.assertEqual(len(self.store.memories(self.identity)),1)

    def test_plan_clear_before_reply_recovers_receipt(self):
        first=self.service.handle('app','ou_owner','om_trial','样例 冰箱')
        token=first.split('确认 ')[1][:6]
        self.store.claim('app','om_confirm','uuid','ou_owner','确认 '+token)
        self.store.state('app','om_confirm','processing')
        confirmed=self.service.handle('app','ou_owner','om_confirm','确认 '+token)
        self.assertIsNone(self.store.pending(self.identity,token))
        sent=[]
        Worker('app',self.store,self.service,lambda *args:sent.append(args) or True).recover()
        self.assertEqual(sent[0][1],confirmed)
        self.assertEqual(len([r for r in self.api.calls if r[1]=='confirm']),1)

    def test_transport_identity_boundary(self):
        raw={'header':{'app_id':'app','event_type':'im.message.receive_v1'},'event':{
            'sender':{'sender_type':'user','sender_id':{'open_id':'ou_owner'}},
            'message':{'message_id':'om_x','chat_id':'oc_x','chat_type':'p2p','message_type':'text','content':json.dumps({'text':'hello'})}}}
        self.assertIsNotNone(normalize(raw,'app'))
        self.assertIsNone(normalize(raw,'wrong'))
        for key,value in [('chat_type','group'),('message_type','image')]:
            changed=copy.deepcopy(raw);changed['event']['message'][key]=value
            self.assertIsNone(normalize(changed,'app'))
        raw['event']['sender']['sender_type']='bot'
        self.assertIsNone(normalize(raw,'app'))

    def test_rich_text_binding_and_unicode_space(self):
        code=self.store.pair_code('rich')
        raw={'header':{'app_id':'rich','event_type':'im.message.receive_v1'},'event':{
            'sender':{'sender_type':'user','sender_id':{'open_id':'ou_rich'}},
            'message':{'message_id':'om_rich','chat_id':'oc_rich','chat_type':'p2p','message_type':'post',
                       'content':json.dumps({'title':'','content':[[{'tag':'text','text':'`绑定\u00a0'+code+'`','style':['code']}]]})}}}
        event=normalize(raw,'rich')
        self.assertIsNotNone(event)
        self.assertIn('已绑定',self.service.handle('rich','ou_rich','om_rich',event['text']))
        changed=copy.deepcopy(raw); changed['event']['message']['content']=json.dumps({'content':[[{'tag':'a','text':'绑定 '+code,'href':'https://evil.example'}]]})
        self.assertIsNone(normalize(changed,'rich'))

    def test_only_loopback_and_short_plan_namespace(self):
        with self.assertRaises(ValueError): EnergyAPI('https://evil.example')
        api=EnergyAPI();payload=[]
        api.request=lambda path,data:payload.append(data)
        api.plan(self.identity,'trial',kind='fridge')
        api.plan({**self.identity,'sender':'ou_other'},'trial',kind='fridge')
        self.assertLessEqual(len(payload[0]['household']),40)
        self.assertNotEqual(payload[0]['household'],payload[1]['household'])


if __name__ == '__main__': unittest.main()
