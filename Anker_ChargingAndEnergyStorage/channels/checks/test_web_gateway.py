import copy
from datetime import datetime,timezone
import json
from pathlib import Path
import tempfile
import time
import unittest
import uuid

from assistant.runtime_api import RuntimeAPI
from assistant.runtime_tools import RuntimeTools
from assistant.service import Service
from assistant.store import Store
from assistant.web_gateway import WebGateway,COOKIE


class FakeModel:
    def intent(self,text,*args):return {'action':'status' if '状态' in text or '现在怎么样' in text else 'advice'}
    def advice(self,*args):return '这是未执行的取舍讨论。'


class FakeEnergy:
    def comparison(self,*args):raise RuntimeError('no_replay')


class FakeRuntime:
    owner=staticmethod(RuntimeAPI.owner)
    def __init__(self):self.confirmations=0;self.proposals={}
    def health(self):return {'environment':'local_debug1_sim','status':'ready'}
    def snapshot(self):
        return {'environment':'local_debug1_sim','sourceSlot':200,'sourceTime':'2025-06-03T02:00:00','capturedAt':time.time(),'paused':True,
                'physicalInputsKind':'previous_ended_interval_hold','telemetryPhysicalAsOfSlot':199,'socBoundarySlot':200,
                'targetWindow':{'start':'2025-06-03T02:00:00','end':'2025-06-05T00:00:00','timeBasis':'device_local_naive','timezone':None},
                'ha':{'telemetry':{'site_1':{'soc_pct':50,'solar_power_w':100,'home_load_w':400},'site_2':{'private':'foreign_sentinel'}},
                      'controls':{'site_1':{'battery_power_w':0}}}}
    def proposal(self,identity,effective,request):
        if request in self.proposals:return copy.deepcopy(self.proposals[request])
        episode=str(uuid.uuid4());result={'environment':'local_debug1_sim','episodeId':episode,'proposalId':str(uuid.uuid4()),
             'owner':self.owner(identity),'sourceSlot':200,'expiresAt':time.time()+600,'constraintsVersion':effective['constraintsVersion'],
             'memoryVersion':effective['memoryVersion'],'consumedMemoryIds':effective['usedMemoryIds'],'unsupported':[],
             'execution':{'targetPowerW':200,'sourceSlot':200,'intervalMinutes':15,'intervalCount':1,'signConvention':'negative_charge_positive_discharge'},
             'events':[{'stage':'proposed'}],'status':'proposed'}
        self.proposals[request]=result;return copy.deepcopy(result)
    def confirm(self,identity,episode,proposal,confirmation):
        self.confirmations+=1
        result=next(r for r in self.proposals.values() if r['episodeId']==episode)
        result.update({'status':'outcome','events':[{'stage':x} for x in ['proposed','confirmed','dispatched','acknowledged','settled','outcome']],
                       'dispatch':{'requestId':episode,'sourceSlot':200,'targetPowerW':200},
                       'evidence':{'requestId':episode,'sourceSlot':200,'nextSlot':201,'readbackMatches':True,
                                   'settlementAvailable':True,'evidenceKind':'simulator_settlement','intervalCostEur':0.02},
                       'outcome':{'settlementAvailable':True}})
        return copy.deepcopy(result)
    def reconcile(self,identity,episode):return copy.deepcopy(next(r for r in self.proposals.values() if r['episodeId']==episode))


class WebGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'db.sqlite')
        self.identity={'app':'qa_app','sender':'ou_qa','household':'site_1'}
        self.api=FakeRuntime();self.runtime=RuntimeTools(self.store,self.api)
        self.service=Service(self.store,FakeEnergy(),FakeModel(),runtime=self.runtime)
        code=self.store.pair_code('qa_app');self.store.bind('qa_app','ou_qa',code)
        self.gateway=WebGateway('qa_app',self.store,self.service)
        code,body,token=self.gateway.dispatch('GET','/api/assistant/session',None)
        self.cookie=COOKIE+'='+token
        self.assertEqual(code,200)

    def tearDown(self):self.store.db.close();self.temp.cleanup()

    def message(self,text=None,action=None,request_id=None):
        request_id=request_id or str(uuid.uuid4())
        body={'requestId':request_id,**({'text':text} if text is not None else {'action':action})}
        return self.gateway.dispatch('POST','/api/assistant/messages',self.cookie,body)[1]

    def confirm(self,pending):
        return self.message(action={'type':'confirm_pending','token':pending['token']})

    def test_short_reserve_dialogue_confirmed_version_and_web_idempotency(self):
        first=self.message('我想设置最低留电量')
        self.assertEqual(first['pending']['kind'],'clarification');self.assertFalse(first['pending']['canConfirm'])
        self.assertIsNone(first['pending']['confirmAction'])
        candidate=self.message('30%');self.assertEqual(candidate['pending']['constraint']['value']['minimumPercent'],30)
        self.assertFalse(self.store.memories(self.identity))
        request_id=str(uuid.uuid4());body={'type':'confirm_pending','token':candidate['pending']['token']}
        saved=self.message(action=body,request_id=request_id)
        repeated=self.message(action=body,request_id=request_id)
        self.assertEqual(saved,repeated);self.assertEqual(len(self.store.memories(self.identity)),1)
        self.assertEqual(saved['memoryVersion'],1)

    def test_home_and_natural_chat_status_share_current_owner_source(self):
        status=self.gateway.dispatch('GET','/api/assistant/status',self.cookie)[1]
        self.assertEqual(status['home']['socPercent'],50)
        self.assertTrue(status['home']['paused']);self.assertEqual(status['home']['measurementSourceSlot'],199)
        self.assertNotIn('foreign_sentinel',json.dumps(status))
        chat=self.message('家里现在怎么样？')
        self.assertEqual(chat['reply']['text'],'最新设备回读：电池还有50%电量，设备待机。本机演练已暂停。')
        self.assertNotIn('2025-',chat['reply']['text'])
        self.assertEqual(self.service.last_result['home']['measurementSourceSlot'],199)

    def test_short_status_discloses_missing_values_without_inventing_state(self):
        snapshot=self.api.snapshot()
        snapshot['ha']['telemetry']['site_1']['soc_pct']=None
        snapshot['ha']['controls']['site_1']['battery_power_w']=None
        snapshot['paused']=None
        self.api.snapshot=lambda:snapshot
        chat=self.message('家里现在怎么样？')
        self.assertEqual(chat['reply']['text'],'最新设备回读：暂时读不到电池电量，设备状态暂不可用。本机演练的暂停状态暂不可用。')
        self.assertIsNone(self.service.last_result['home']['socPercent'])

    def test_natural_plan_only_proposes_and_deletion_invalidates_old_plan(self):
        candidate=self.message('保底 30%');self.confirm(candidate['pending'])
        plan=self.message('帮我安排这一时段')
        self.assertEqual(plan['pending']['kind'],'simulation_plan');self.assertEqual(self.api.confirmations,0)
        memory=self.store.memories(self.identity)[0]
        self.message(action={'type':'delete_memory','memoryId':memory['id']})
        # Obtain the fresh same pending token, then the service rechecks the changed version.
        state=self.gateway.dispatch('GET','/api/assistant/session',self.cookie)[1]
        rejected=self.confirm(state['pending'])
        self.assertIn('已变化',rejected['reply']['text']);self.assertEqual(self.api.confirmations,0)

    def test_feedback_is_saved_then_separate_revision_confirmation(self):
        plan=self.message('帮我安排这一时段');executed=self.confirm(plan['pending'])
        episode=executed['episode']['episodeId'];version=self.service.memory_v2.version(self.identity)
        feedback=self.message(action={'type':'feedback','episodeId':episode,'rating':'unsuitable','reasonText':'希望保底40%'})
        self.assertEqual(feedback['pending']['kind'],'feedback')
        saved=self.confirm(feedback['pending'])
        self.assertEqual(saved['pending']['kind'],'revision_candidate')
        self.assertEqual(self.service.memory_v2.version(self.identity),version)
        self.confirm(saved['pending'])
        self.assertEqual(self.service.memory_v2.version(self.identity),version+1)
        self.assertEqual(len(self.service.memory_v2.feedback_records(self.identity)),1)

    def test_cache_expiry_is_completed_tombstone_not_another_execution(self):
        request_id=str(uuid.uuid4());first=self.message('帮我安排这一时段',request_id=request_id)
        with self.store.db:self.store.db.execute('UPDATE web_requests SET created=?',(time.time()-601,))
        restored=self.gateway.dispatch('GET','/api/assistant/messages?requestId='+request_id,self.cookie)[1]
        self.assertEqual(restored['status'],'completed');self.assertTrue(restored['cacheExpired'])
        self.assertFalse(restored['retryable']);self.assertEqual(restored['episode']['episodeId'],first['episode']['episodeId'])
        repeated=self.message('帮我安排这一时段',request_id=request_id)
        self.assertTrue(repeated['cacheExpired']);self.assertEqual(len(self.api.proposals),1)

    def test_browser_cannot_supply_identity_or_confirm_unshown_plan(self):
        body={'requestId':str(uuid.uuid4()),'text':'收益','sender':'ou_other'}
        code,result,_=self.gateway.dispatch('POST','/api/assistant/messages',self.cookie,body)
        self.assertEqual(code,400);self.assertEqual(result['error']['code'],'invalid_message_fields')
        plan=self.runtime.propose(self.identity,'feishu_fake_input')
        pending=self.store.pending_record(self.identity)
        result=self.message(action={'type':'confirm_pending','token':pending['token']})
        self.assertEqual(result['error']['code'],'pending_not_shown_or_stale');self.assertEqual(self.api.confirmations,0)

    def test_unbound_permission_schema_is_parseable(self):
        other=WebGateway('unbound_app',self.store,self.service)
        code,result,_=other.dispatch('GET','/api/assistant/session',None)
        self.assertEqual(code,409);self.assertEqual(result['status'],'unbound')
        for key in ('ownerLabel','memoryVersion','pending'):self.assertIsNone(result[key])

    def test_command_normalization_cannot_bypass_unshown_pending(self):
        for command in ('记下 `{token}`','`记下 {token}`','记下\u3000`{token}`','确认 `{token}`','选1\u00a0`{token}`','执行 `{token}`'):
            token=self.store.put_pending(self.identity,{'action':'memory','kind':'habit','key':'qa','value':'qa'})
            result=self.message(command.format(token=token))
            self.assertEqual(result['error']['code'],'pending_not_shown_or_stale')
        self.assertEqual(self.store.memories(self.identity),[])

    def test_public_episode_excludes_private_source_message(self):
        plan=self.message('帮我安排这一时段');executed=self.confirm(plan['pending'])
        internal=self.service.memory_v2.episode(self.identity,executed['episode']['episodeId'])
        self.assertTrue(any('sourceMessageId' in event['evidence'] for event in internal['events']))
        self.assertNotIn('sourceMessageId',json.dumps(executed['episode']))

    def test_memory_chat_does_not_publish_native_feishu_message_id(self):
        token=self.store.put_pending(self.identity,{'action':'memory','kind':'habit','key':'QA','value':'QA',
                                                    'source_message_id':'om_private_source_sentinel'})
        self.store.apply_pending(self.identity,token,'qa_explicit_confirm')
        response=self.message('资料')
        self.assertNotIn('om_private_source_sentinel',json.dumps(response))
        self.assertEqual(self.store.memories(self.identity)[0]['source_message_id'],'om_private_source_sentinel')


if __name__=='__main__':unittest.main()
