from datetime import datetime,timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from assistant.memory_v2 import MemoryV2
from assistant.store import Store


class MemoryV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'legacy.sqlite'
        self.store=Store(self.path)
        self.identity={'app':'app','sender':'ou_owner','household':'site_1'}
        self.record=self.save('通常18点到家')
        self.v2=MemoryV2(self.store.db)
        self.backup=Path(self.temp.name)/'before.sqlite'
        self.v2.migrate(self.backup)

    def tearDown(self):
        self.store.db.close();self.temp.cleanup()

    def save(self,value,**kwargs):
        data={'action':'memory','kind':'habit','key':'到家','value':value,**kwargs}
        token=self.store.put_pending(self.identity,data)
        return self.store.apply_pending(self.identity,token,'om_'+token)

    def test_additive_migration_preserves_legacy_and_backup(self):
        self.assertEqual(self.store.memories(self.identity)[0]['value'],'通常18点到家')
        self.assertEqual(self.v2.history(self.identity,self.record['id'])[0]['revision'],1)
        self.assertFalse(self.v2.migrate())
        copy=sqlite3.connect(self.backup)
        self.assertEqual(copy.execute('SELECT value FROM memories').fetchone()[0],'通常18点到家');copy.close()

    def test_version_changes_only_on_confirmed_revisions_and_delete(self):
        original=self.v2.version(self.identity)
        self.store.put_pending(self.identity,{'action':'memory','kind':'preference','key':'目标','value':'备电'})
        self.assertEqual(original,self.v2.version(self.identity))
        self.save('通常19点到家',id=self.record['id'])
        with self.store.db: self.v2.record_confirmation(self.identity,self.record['id'])
        history=self.v2.history(self.identity,self.record['id'])
        self.assertEqual([r['state'] for r in history],['superseded','current'])
        self.assertGreater(self.v2.version(self.identity),original)
        self.store.delete(self.identity,self.record['id'])
        with self.store.db:self.v2.record_deletion(self.identity,[self.record['id']])
        self.assertTrue(all(r['state']=='deleted' and r['value'] is None and r['key'] is None for r in self.v2.history(self.identity,self.record['id'])))

    def test_window_and_machine_constraints_distinguish_context_only(self):
        preference=self.save('最低保留两成',kind='preference',key='备用电量')
        with self.store.db:self.v2.record_confirmation(self.identity,preference['id'],{'type':'reserve_soc','value':{'minimumPercent':20}})
        activity=self.save('明天外出',kind='activity',starts_at='2026-10-18T00:00:00+00:00',expires_at='2026-10-19T00:00:00+00:00')
        with self.store.db:self.v2.record_confirmation(self.identity,activity['id'])
        result=self.v2.effective_constraints(self.identity,'2026-10-18T08:00:00+08:00','2026-10-19T08:00:00+08:00')
        self.assertEqual(result['usedMemoryIds'],[preference['id']])
        self.assertIn(activity['id'],result['contextOnlyMemoryIds'])
        self.assertEqual(result['constraints'][0]['value']['minimumPercent'],20)
        other={**self.identity,'sender':'ou_other'}
        self.assertFalse(self.v2.effective_constraints(other,'2026-10-18T00:00:00+00:00','2026-10-19T00:00:00+00:00')['usedMemoryIds'])

    def test_episode_stages_are_owned_and_evidence_distinct(self):
        constraints=self.v2.effective_constraints(self.identity,'2026-10-18T00:00:00+00:00','2026-10-19T00:00:00+00:00')
        episode=self.v2.create_episode(self.identity,constraints)
        with self.assertRaises(ValueError):self.v2.episode_event(self.identity,episode,'settled',{})
        with self.assertRaises(ValueError):self.v2.episode_event({**self.identity,'sender':'ou_other'},episode,'proposed',{})
        for phase,evidence in [('proposed',{'proposalId':'p1','consumedMemoryIds':[]}),('confirmed',{'confirmationId':'m1','proposalId':'p1'}),
                               ('dispatched',{'requestId':'r1','environment':'local_debug1_sim'}),
                               ('acknowledged',{'requestId':'r1','readbackMatches':True,'evidenceKind':'ha_readback'}),
                               ('settled',{'requestId':'r1','costEur':1.5,'settlementAvailable':True,'evidenceKind':'simulator_settlement'}),
                               ('outcome',{'settlementAvailable':True})]:
            self.v2.episode_event(self.identity,episode,phase,evidence)
        before=self.v2.version(self.identity)
        feedback=self.v2.add_feedback(self.identity,episode,'更偏好备电','m_feedback')
        self.assertEqual(feedback,self.v2.add_feedback(self.identity,episode,'重复','m_feedback'))
        self.assertEqual(self.v2.version(self.identity),before)
        self.assertEqual(self.v2.episode(self.identity,episode)['state'],'outcome')

    def test_bounded_recall_does_not_drop_machine_constraints(self):
        for index in range(8):
            record=self.save('普通习惯'+str(index)+'；'+('睡前关闭照明'*12),key='照明')
            with self.store.db:self.v2.record_confirmation(self.identity,record['id'])
        relevant=self.save('电动车通常傍晚回家',key='充电习惯')
        with self.store.db:self.v2.record_confirmation(self.identity,relevant['id'])
        hard=self.save('保留三成',kind='preference',key='备电保底')
        with self.store.db:self.v2.record_confirmation(self.identity,hard['id'],{'type':'reserve_soc','value':{'minimumPercent':30}})
        recalled=self.v2.recall(self.identity,'电动车充电','2026-10-18T00:00:00+00:00','2026-10-19T00:00:00+00:00',max_chars=180)
        self.assertEqual(recalled['contextFacts'][0]['memoryId'],relevant['id'])
        self.assertIn(hard['id'],recalled['effectiveConstraints']['usedMemoryIds'])
        self.assertTrue(recalled['omittedContextMemoryIds'])

    def test_offset_comparison_and_naive_device_do_not_map_activity(self):
        activity=self.save('外出',kind='activity',starts_at='2026-10-18T08:00:00+08:00',expires_at='2026-10-18T09:00:00+08:00')
        with self.store.db:self.v2.record_confirmation(self.identity,activity['id'])
        active=self.v2.effective_constraints(self.identity,'2026-10-18T00:30:00+00:00','2026-10-18T01:30:00+00:00')
        self.assertIn(activity['id'],active['contextOnlyMemoryIds'])
        expired=self.v2.effective_constraints(self.identity,'2026-10-18T01:00:00+00:00','2026-10-18T02:00:00+00:00')
        self.assertNotIn(activity['id'],expired['contextOnlyMemoryIds'])
        naive=self.v2.effective_constraints(self.identity,'2025-07-28 12:00:00','2025-07-28 13:00:00','device_local_naive')
        self.assertIn({'memoryId':activity['id'],'reason':'time_basis_unknown'},naive['missingFields'])


if __name__=='__main__':unittest.main()
