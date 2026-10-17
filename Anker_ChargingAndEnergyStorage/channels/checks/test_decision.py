"""RD-01 local fixtures: real LP/ledger, temporary owner, no HA or provider."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'checks/runtime'))
from test_core_contract import SyntheticSource
from energy_runtime.runtime import Runtime,Rejected
from assistant.runtime_api import RuntimeAPI,RuntimeUnavailable
from assistant.runtime_tools import RuntimeTools
from assistant.service import Service
from assistant.store import Store
from assistant.web_gateway import WebGateway,COOKIE
from channels.feishu import Worker


class Source(SyntheticSource):
    def __init__(self):super().__init__();self.slot=9;self.entered=threading.Event();self.release=None
    def snapshot(self):
        result=super().snapshot()
        result.update(sourceSlot=self.slot,sourceTime=result['targetWindow']['start'],capturedAt=1,paused=True,
            telemetryPhysicalAsOfSlot=self.slot-1,socBoundarySlot=self.slot,physicalInputsKind='previous_ended_interval_hold',
            ha={'telemetry':{'site_1':{'soc_pct':50,'solar_power_w':0,'home_load_w':500,'capacity_kwh':5}},
                'controls':{'site_1':{'power_w':0,'direction':'discharge','battery_power_w':0}}})
        return result
    def execute(self,episode):
        self.entered.set()
        if self.release:self.release.wait(5)
        result=super().execute(episode)
        result['veAfter'].update(grid_import_w=result['historyRow']['grid_import_w'],grid_export_w=result['historyRow']['grid_export_w'])
        result['haAfter']['controls']['captured_at']=2
        result['haAfter']['telemetry']['site_1']={'soc_pct':result['veAfter']['soc_pct'],'solar_power_w':0,'home_load_w':500}
        return result


class API:
    owner=staticmethod(RuntimeAPI.owner)
    def __init__(self,core,source):self.core,self.source=core,source
    def health(self):return {'status':'ready'}
    def snapshot(self):return self.source.snapshot()
    def preview(self,who,effective,percent,replace,request):
        try:return self.core.preview(self.owner(who),effective,{'minimumPercent':percent,'replaceMemoryId':replace},request)
        except Rejected as error:raise RuntimeUnavailable(str(error)) from None
    def decision(self,who,episode_id=None,preview_id=None):return self.core.decision(self.owner(who)['ownerKey'],episode_id,preview_id)
    def proposal(self,who,effective,request,preview_id=None):
        try:return self.core.propose(self.owner(who),effective,request,preview_id)
        except Rejected as error:raise RuntimeUnavailable(str(error)) from None
    def confirm(self,who,episode,proposal,confirmation):return self.core.confirm(self.owner(who),episode,proposal,confirmation)
    def reconcile(self,who,episode):return self.core.reconcile(self.owner(who),episode)


class NoModel:
    def intent(self,*args):return {'action':'status'}
    def advice(self,*args):return '资料未变。'


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'assistant.db'
        self.store=Store(self.path);self.who={'app':'qa_rd','sender':'ou_qa_private','household':'site_1'}
        self.store.bind(self.who['app'],self.who['sender'],self.store.pair_code(self.who['app']))
        self.source=Source();self.core=Runtime(self.source,Path(self.temp.name)/'core.db');self.api=API(self.core,self.source)
        self.service=Service(self.store,object(),NoModel(),runtime=RuntimeTools(self.store,self.api))
        self.gateway=WebGateway(self.who['app'],self.store,self.service)
        _,_,token=self.gateway.dispatch('GET','/api/assistant/session',None);self.cookie=COOKIE+'='+token
    def tearDown(self):self.store.db.close();self.temp.cleanup()
    def post(self,text=None,action=None,request=None):
        return self.gateway.dispatch('POST','/api/assistant/messages',self.cookie,{'requestId':request or str(uuid.uuid4()),**({'text':text} if text is not None else {'action':action})})[1]
    def confirm(self,result):return self.post(action={'type':'confirm_pending','token':result['pending']['token']})
    def view(self):return self.gateway.dispatch('GET','/api/assistant/decision',self.cookie)[1]

    def test_preview_save_then_distinct_execution_confirmation(self):
        draft=self.post('保底 50%');preview=self.view()
        self.assertEqual(draft['pending']['kind'],'memory');self.assertIsNotNone(draft['pending']['previewId'])
        self.assertTrue(preview['plan']['hypothetical']);self.assertFalse(preview['plan']['executable'])
        self.assertEqual(self.store.memories(self.who),[]);self.assertEqual(self.source.execute_calls,0)
        self.assertIn('不匹配',self.post('执行 '+draft['pending']['token'])['reply']['text'])
        saved=self.confirm(draft);self.assertEqual(saved['pending']['kind'],'simulation_plan')
        self.assertNotEqual(saved['pending']['token'],draft['pending']['token']);self.assertEqual(self.source.execute_calls,0)
        actual=self.view();self.assertFalse(actual['plan']['hypothetical']);self.assertEqual(actual['constraints']['memoryVersion'],1)
        complete=self.confirm(saved);self.assertEqual(complete['episode']['stage'],'outcome')
        self.assertEqual(self.view()['actual']['executedSlot'],9);self.assertEqual(self.source.execute_calls,1)

    def test_selected_get_is_readonly_and_does_not_mark_seen(self):
        self.post('保底 50%');before=(self.source.solve_calls,self.source.snapshot_calls,self.store.db.total_changes)
        seen=tuple(self.store.db.execute('SELECT seen_token,seen_proposal FROM web_sessions').fetchone())
        first=self.view();second=self.view()
        self.assertEqual(first['viewVersion'],second['viewVersion'])
        self.assertEqual(before,(self.source.solve_calls,self.source.snapshot_calls,self.store.db.total_changes))
        self.assertEqual(seen,tuple(self.store.db.execute('SELECT seen_token,seen_proposal FROM web_sessions').fetchone()))

    def test_explain_preview_uses_same_selection_without_model_or_new_calculation(self):
        empty=self.post('解释这次安排');self.assertIn('还没有当前安排',empty['reply']['text'])
        draft=self.post('保底 50%');view=self.view()
        before=(self.source.solve_calls,self.source.snapshot_calls,self.source.execute_calls,self.service.memory_v2.version(self.who))
        answer=self.post('这项留电要求改变了什么？')
        self.assertEqual(answer['previewId'],draft['previewId'])
        self.assertIn('同一预计窗口',answer['reply']['text']);self.assertIn('LP最低留电量由0 kWh变为2.5 kWh',answer['reply']['text'])
        self.assertIn('不是实际收益',answer['reply']['text']);self.assertIsNone(view['actual'])
        self.assertEqual(before,(self.source.solve_calls,self.source.snapshot_calls,self.source.execute_calls,self.service.memory_v2.version(self.who)))

    def test_explain_settled_uses_receipt_and_does_not_resurrect_deleted_value(self):
        saved=self.confirm(self.post('保底 50%'));executed=self.confirm(saved)
        record=self.store.memories(self.who)[0];self.post(action={'type':'delete_memory','memoryId':record['id']})
        before=(self.source.solve_calls,self.source.snapshot_calls,self.source.execute_calls)
        answer=self.post('这份安排改变了什么')
        self.assertEqual(answer['episode']['episodeId'],executed['episode']['episodeId'])
        self.assertIn('已核验的这15分钟',answer['reply']['text']);self.assertIn('不是节省金额',answer['reply']['text'])
        self.assertNotIn('软件保底SOC',answer['reply']['text']);self.assertNotIn(record['id'],answer['reply']['text'])
        self.assertEqual(before,(self.source.solve_calls,self.source.snapshot_calls,self.source.execute_calls))

    def test_empty_owner_auth_and_duplicate_query_boundaries(self):
        self.assertEqual(self.view()['availability'],'empty');self.assertEqual(self.source.solve_calls,0)
        self.assertEqual(self.gateway.read_dispatch('/api/assistant/decision',None)[0],401)
        self.assertEqual(self.gateway.read_dispatch('/api/assistant/decision?episodeId=a&episodeId=b',self.cookie)[0],400)
        self.assertEqual(self.gateway.read_dispatch('/api/assistant/feed?after=a&after=b',self.cookie)[0],400)
        self.assertEqual(self.gateway.read_dispatch('/api/assistant/decision?sender=ou_other',self.cookie)[0],400)
        self.assertEqual(self.gateway.read_dispatch('/api/assistant/decision?episodeId=foreign',self.cookie)[0],404)

    def test_source_change_after_save_keeps_fact_but_no_executable_preview(self):
        draft=self.post('保底 50%');self.source.slot=10;saved=self.confirm(draft)
        self.assertIn('偏好已保存',saved['reply']['text']);self.assertEqual(len(self.store.memories(self.who)),1)
        self.assertIsNone(saved['pending']);self.assertEqual(self.source.execute_calls,0)
        self.assertEqual(self.view()['availability'],'stale')

    def test_confirmed_dispatched_visible_during_long_confirm(self):
        saved=self.confirm(self.post('保底 50%'));self.source.release=threading.Event()
        with ThreadPoolExecutor(max_workers=1) as executor:
            task=executor.submit(self.confirm,saved);self.assertTrue(self.source.entered.wait(3))
            started=time.monotonic();view=self.view()
            self.assertLess(time.monotonic()-started,.5);self.assertEqual(view['execution']['phase'],'dispatched')
            self.assertIsNone(view['actual']);self.source.release.set();task.result(timeout=4)

    def test_delete_scrubs_historical_items_and_feed_without_erasing_actual(self):
        saved=self.confirm(self.post('保底 50%'));self.confirm(saved)
        memory=self.store.memories(self.who)[0];self.post(action={'type':'delete_memory','memoryId':memory['id']})
        view=self.view();self.assertEqual(view['constraints']['items'],[])
        self.assertEqual(view['availability'],'ready');self.assertIsNotNone(view['actual'])
        self.assertEqual(view['constraints']['reserveEnergyKwh'],2.5)
        feed=self.gateway.read_dispatch('/api/assistant/feed',self.cookie)[1]
        self.assertNotIn('保底 50%',json.dumps(feed,ensure_ascii=False))

    def test_mock_feishu_sync_web_dedup_redaction_retention_and_scope(self):
        worker=Worker(self.who['app'],self.store,self.service,lambda *args:True)
        event={'sender':self.who['sender'],'message':'om_native_private','text':'我想多留一点备用电','chat':'oc_native_private'}
        worker.receive(event);worker.receive(event)
        request=str(uuid.uuid4());self.post('资料',request=request);self.post('资料',request=request)
        feed=self.gateway.read_dispatch('/api/assistant/feed',self.cookie)[1]
        self.assertEqual(len(feed['items']),2);self.assertEqual(feed['items'][0]['sourceChannel'],'feishu')
        self.assertEqual(feed['items'][1]['requestId'],request);self.assertNotIn('om_native',json.dumps(feed))
        self.store.interaction(self.who,'qa_codes','记下 `ABCDEF`','请回复“执行 ABCDEF”或绑定 12345678。')
        clean=self.gateway.read_dispatch('/api/assistant/feed',self.cookie)[1]
        self.assertNotIn('ABCDEF',json.dumps(clean));self.assertNotIn('12345678',json.dumps(clean))
        with self.store.db:self.store.db.execute('UPDATE interaction_feed SET created_at=?',(time.time()-601,))
        expired=self.gateway.read_dispatch('/api/assistant/feed?after='+feed['cursor'],self.cookie)[1]
        self.assertTrue(expired['reset']);self.assertEqual(expired['items'],[])


if __name__=='__main__':unittest.main()
