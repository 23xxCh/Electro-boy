"""Versioned personal facts and decision evidence on the existing SQLite connection."""
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager
import hashlib
import json
import sqlite3
import time
import uuid
import re
import math


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


class MemoryV2:
    def __init__(self, db):
        self.db = db

    @contextmanager
    def transaction(self):
        if self.db.in_transaction:
            yield
        else:
            with self.db:
                yield

    def migrate(self, backup_path=None):
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version >= 2:
            return False
        if self.db.in_transaction:
            raise RuntimeError('migration_requires_idle_transaction')
        if backup_path:
            if Path(backup_path).exists():
                raise ValueError('migration_backup_already_exists')
            destination = sqlite3.connect(backup_path)
            try:
                self.db.backup(destination)
            finally:
                destination.close()
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS memory_owners(app TEXT,sender TEXT,household TEXT,version INTEGER NOT NULL,
          PRIMARY KEY(app,sender,household));
        CREATE TABLE IF NOT EXISTS memory_revisions(memory_id TEXT,revision INTEGER,app TEXT,sender TEXT,
          household TEXT,kind TEXT,key TEXT,value TEXT,starts_at TEXT,expires_at TEXT,
          source_message_id TEXT,recorded_at REAL,state TEXT,constraint_json TEXT,
          PRIMARY KEY(memory_id,revision));
        CREATE TABLE IF NOT EXISTS episodes(id TEXT PRIMARY KEY,app TEXT,sender TEXT,household TEXT,
          created_at REAL,updated_at REAL,state TEXT,proposal_id TEXT,constraints_version TEXT,
          memory_version INTEGER,used_memory_ids TEXT,consumed_memory_ids TEXT);
        CREATE TABLE IF NOT EXISTS episode_events(episode_id TEXT,sequence INTEGER,phase TEXT,
          created_at REAL,evidence_json TEXT,PRIMARY KEY(episode_id,sequence));
        CREATE TABLE IF NOT EXISTS feedback(id TEXT PRIMARY KEY,episode_id TEXT,app TEXT,sender TEXT,
          household TEXT,summary TEXT,source_message_id TEXT,created_at REAL,state TEXT);
        CREATE TABLE IF NOT EXISTS revision_candidates(id TEXT PRIMARY KEY,app TEXT,sender TEXT,
          household TEXT,memory_id TEXT,feedback_id TEXT,proposed_json TEXT,source_message_id TEXT,
          created_at REAL,expires_at REAL,state TEXT);
        ''')
        with self.transaction():
            for row in self.db.execute('SELECT * FROM memories').fetchall():
                identity = {'app':row['app'],'sender':row['sender'],'household':row['household']}
                self.record_confirmation(identity,row['id'])
            self.db.execute('PRAGMA user_version=2')
        return True

    @staticmethod
    def scope(identity):
        return identity['app'],identity['sender'],identity['household']

    def version(self, identity):
        row = self.db.execute('SELECT version FROM memory_owners WHERE app=? AND sender=? AND household=?',self.scope(identity)).fetchone()
        return row[0] if row else 0

    def bump(self, identity):
        self.db.execute('INSERT INTO memory_owners VALUES(?,?,?,1) ON CONFLICT(app,sender,household) DO UPDATE SET version=version+1',self.scope(identity))

    def record_confirmation(self, identity, memory_id, constraint=None):
        row = self.db.execute('SELECT * FROM memories WHERE id=? AND app=? AND sender=? AND household=?',
                              (memory_id,*self.scope(identity))).fetchone()
        if not row:
            raise ValueError('memory_not_owned')
        revision = self.db.execute('SELECT COALESCE(MAX(revision),0)+1 FROM memory_revisions WHERE memory_id=?', (memory_id,)).fetchone()[0]
        self.db.execute('UPDATE memory_revisions SET state=? WHERE memory_id=? AND app=? AND sender=? AND household=? AND state=?',
                        ('superseded',memory_id,*self.scope(identity),'current'))
        self.db.execute('INSERT INTO memory_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (memory_id,revision,*self.scope(identity),row['kind'],row['key'],row['value'],
                         row['starts_at'],row['expires_at'],row['source_message_id'],time.time(),'current',
                         canonical(constraint) if constraint is not None else None))
        self.bump(identity)
        return revision

    def record_deletion(self, identity, memory_ids):
        changed=False
        for memory_id in memory_ids:
            result=self.db.execute('UPDATE memory_revisions SET state=?,key=NULL,value=NULL,starts_at=NULL,expires_at=NULL,constraint_json=NULL WHERE memory_id=? AND app=? AND sender=? AND household=? AND state!=?',
                                   ('deleted',memory_id,*self.scope(identity),'deleted'))
            changed = changed or result.rowcount > 0
            self.db.execute('UPDATE revision_candidates SET proposed_json=?,state=? WHERE memory_id=? AND app=? AND sender=? AND household=?',
                            ('{}','discarded',memory_id,*self.scope(identity)))
        if changed:
            self.bump(identity)

    def history(self, identity, memory_id):
        return [dict(row) for row in self.db.execute('SELECT * FROM memory_revisions WHERE memory_id=? AND app=? AND sender=? AND household=? ORDER BY revision',
                                                   (memory_id,*self.scope(identity)))]

    def effective_constraints(self, identity, start, end, time_basis='explicit_household_timezone_normalized_utc'):
        start_time,end_time=(datetime.fromisoformat(value) for value in (start,end))
        if time_basis=='device_local_naive':
            if start_time.tzinfo or end_time.tzinfo or end_time<=start_time:
                raise ValueError('naive_device_target_window_required')
        elif time_basis=='explicit_household_timezone_normalized_utc':
            if not start_time.tzinfo or not end_time.tzinfo or end_time<=start_time:
                raise ValueError('explicit_aware_target_window_required')
            start_time,end_time=(value.astimezone(timezone.utc) for value in (start_time,end_time))
            start,end=(value.isoformat() for value in (start_time,end_time))
        else:
            raise ValueError('unsupported_target_time_basis')
        constraints,context_ids,missing=[],[],[]
        rows=self.db.execute('SELECT * FROM memory_revisions WHERE app=? AND sender=? AND household=? AND state=? ORDER BY memory_id',
                             (*self.scope(identity),'current')).fetchall()
        for row in rows:
            if row['starts_at'] or row['expires_at']:
                if time_basis=='device_local_naive':
                    context_ids.append(row['memory_id'])
                    missing.append({'memoryId':row['memory_id'],'reason':'time_basis_unknown'})
                    continue
                try:
                    starts=datetime.fromisoformat(row['starts_at']) if row['starts_at'] else None
                    expires=datetime.fromisoformat(row['expires_at']) if row['expires_at'] else None
                    if starts and not starts.tzinfo or expires and not expires.tzinfo:
                        raise ValueError('unknown_validity_timezone')
                    if starts and starts>=end_time or expires and expires<=start_time:
                        continue
                except (ValueError,TypeError):
                    missing.append({'memoryId':row['memory_id'],'reason':'invalid_validity_window'})
                    continue
            if not row['constraint_json']:
                context_ids.append(row['memory_id'])
                continue
            constraint=json.loads(row['constraint_json'])
            if not isinstance(constraint,dict) or not isinstance(constraint.get('type'),str):
                missing.append({'memoryId':row['memory_id'],'reason':'invalid_confirmed_constraint'})
                continue
            constraints.append({'memoryId':row['memory_id'],'revision':row['revision'],**constraint,
                                'startsAt':row['starts_at'],'expiresAt':row['expires_at']})
        window={'start':start,'end':end,'timeBasis':time_basis,'timezone':None if time_basis=='device_local_naive' else 'UTC'}
        content={'memoryVersion':self.version(identity),'targetWindow':window,'constraints':constraints,
                 'contextOnlyMemoryIds':context_ids}
        return {'schemaVersion':'ch02-effective-constraints-v1',**content,
                'constraintsVersion':hashlib.sha256(canonical(content).encode()).hexdigest(),
                'usedMemoryIds':list(dict.fromkeys(c['memoryId'] for c in constraints)), 'missingFields':missing}

    def create_episode(self, identity, constraints, episode_id=None):
        episode_id=episode_id or uuid.uuid4().hex
        if not isinstance(episode_id,str) or not 1<=len(episode_id)<=100:
            raise ValueError('invalid_episode_id')
        now=time.time()
        with self.transaction():
            self.db.execute('INSERT INTO episodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                            (episode_id,*self.scope(identity),now,now,'created',None,
                             constraints['constraintsVersion'],constraints['memoryVersion'],
                             canonical(constraints['usedMemoryIds']),'[]'))
        return episode_id

    def recall(self, identity, query, start, end, max_chars=1600, time_basis='explicit_household_timezone_normalized_utc'):
        effective=self.effective_constraints(identity,start,end,time_basis)
        allowed=set(effective['contextOnlyMemoryIds'])
        words=set(re.findall(r'[a-z0-9_]+',query.lower()))
        for sequence in re.findall(r'[\u4e00-\u9fff]+',query):
            words.update(sequence[index:index+2] for index in range(len(sequence)-1))
        records=[dict(row) for row in self.db.execute('SELECT * FROM memory_revisions WHERE app=? AND sender=? AND household=? AND state=?',
                                                    (*self.scope(identity),'current')) if row['memory_id'] in allowed]
        def score(record):
            text=(record['key']+' '+record['value']).lower()
            return (-sum(word in text for word in words),-record['recorded_at'],record['memory_id'])
        selected,omitted,total=[],[],0
        for record in sorted(records,key=score):
            summary=f"{record['memory_id']}：{record['key']}＝{record['value']}"
            if record['kind']=='activity':
                summary+=f"；{record['starts_at']} 至 {record['expires_at']}"
            if total+len(summary)+1<=max_chars:
                selected.append({'memoryId':record['memory_id'],'revision':record['revision'],'summary':summary})
                total+=len(summary)+1
            else:
                omitted.append(record['memory_id'])
        return {'schemaVersion':'ch02-recall-v1','effectiveConstraints':effective,
                'contextFacts':selected,'usedContextMemoryIds':[r['memoryId'] for r in selected],
                'omittedContextMemoryIds':omitted,'recallRule':'owner_time_window_then_keyword_bigrams_and_recency'}

    def episode(self, identity, episode_id):
        row=self.db.execute('SELECT * FROM episodes WHERE id=? AND app=? AND sender=? AND household=?',
                             (episode_id,*self.scope(identity))).fetchone()
        if not row:
            return None
        result=dict(row)
        result['used_memory_ids']=json.loads(result['used_memory_ids'])
        result['consumed_memory_ids']=json.loads(result['consumed_memory_ids'])
        result['events']=[{'sequence':r['sequence'],'phase':r['phase'],'createdAt':r['created_at'],
                           'evidence':json.loads(r['evidence_json'])} for r in self.db.execute(
                               'SELECT * FROM episode_events WHERE episode_id=? ORDER BY sequence',(episode_id,))]
        return result

    def episode_event(self, identity, episode_id, phase, evidence):
        transitions={'created':{'proposed','rejected','failed'},'proposed':{'confirmed','rejected','expired','failed'},
                     'confirmed':{'dispatched','rejected','failed','unknown'},'dispatched':{'acknowledged','failed','unknown'},
                     'acknowledged':{'settled','outcome','failed','unknown'},'settled':{'outcome'},'unknown':{'dispatched','acknowledged','failed'}}
        with self.transaction():
            episode=self.episode(identity,episode_id)
            if not episode:
                raise ValueError('episode_not_owned')
            if phase not in transitions.get(episode['state'],set()):
                raise ValueError('invalid_episode_transition')
            if not isinstance(evidence,dict):
                raise ValueError('structured_evidence_required')
            allowed={
                'proposed':{'proposalId','consumedMemoryIds','unsupportedMemoryIds','rejectedMemoryIds','sourceSlot','constraintsVersion','environment'},
                'confirmed':{'confirmationId','proposalId','sourceMessageId'},
                'dispatched':{'requestId','environment','sourceSlot','haStatus','targetKw'},
                'acknowledged':{'requestId','evidenceKind','readbackMatches','sourceSlot','observedBatteryKw','targetKw'},
                'settled':{'requestId','evidenceKind','settlementAvailable','costEur','sourceSlot','importKwh','exportKwh'},
                'outcome':{'settlementAvailable','success','reasonCode','sourceSlot'},
                'rejected':{'reasonCode'},'expired':{'reasonCode'},'failed':{'errorCode','reasonCode','requestId'},
                'unknown':{'reasonCode','requestId','environment'},
            }
            if set(evidence)-allowed[phase]:
                raise ValueError('nontechnical_episode_field_rejected')
            if phase=='proposed' and (not isinstance(evidence.get('proposalId'),str) or not evidence['proposalId']):
                raise ValueError('proposal_evidence_required')
            if phase=='proposed' and (not isinstance(evidence.get('consumedMemoryIds'),list) or
                                      not all(isinstance(item,str) for item in evidence['consumedMemoryIds']) or
                                      set(evidence['consumedMemoryIds'])-set(episode['used_memory_ids'])):
                raise ValueError('unbound_consumed_memory_ids')
            if phase=='confirmed' and (evidence.get('proposalId')!=episode['proposal_id'] or not evidence.get('confirmationId')):
                raise ValueError('confirmation_binding_required')
            if phase=='dispatched' and (not isinstance(evidence.get('requestId'),str) or not evidence['requestId'] or
                                        evidence.get('environment')!='local_debug1_sim'):
                raise ValueError('local_dispatch_evidence_required')
            dispatched=next((event['evidence'] for event in reversed(episode['events']) if event['phase']=='dispatched'),None)
            if phase in {'acknowledged','settled'} and (not dispatched or evidence.get('requestId')!=dispatched.get('requestId')):
                raise ValueError('execution_request_mismatch')
            if phase=='acknowledged' and (evidence.get('readbackMatches') is not True or evidence.get('evidenceKind')!='ha_readback'):
                raise ValueError('actual_readback_evidence_required')
            if phase=='settled':
                cost=evidence.get('costEur')
                if (evidence.get('evidenceKind')!='simulator_settlement' or evidence.get('settlementAvailable') is not True or
                        isinstance(cost,bool) or not isinstance(cost,(int,float)) or not math.isfinite(cost)):
                    raise ValueError('actual_settlement_evidence_required')
            if phase=='outcome' and (not isinstance(evidence.get('settlementAvailable'),bool) or
                                      evidence['settlementAvailable'] and episode['state']!='settled'):
                raise ValueError('outcome_settlement_state_mismatch')
            sequence=len(episode['events'])+1
            self.db.execute('INSERT INTO episode_events VALUES(?,?,?,?,?)',
                            (episode_id,sequence,phase,time.time(),canonical(evidence)))
            self.db.execute('UPDATE episodes SET state=?,updated_at=? WHERE id=?',(phase,time.time(),episode_id))
            if phase=='proposed':
                self.db.execute('UPDATE episodes SET proposal_id=?,consumed_memory_ids=? WHERE id=?',
                                (evidence.get('proposalId'),canonical(evidence.get('consumedMemoryIds',[])),episode_id))
        return sequence

    def add_feedback(self, identity, episode_id, summary, source_message_id):
        if not self.episode(identity,episode_id):
            raise ValueError('episode_not_owned')
        if not isinstance(summary,str) or not 0<len(summary.strip())<=400:
            raise ValueError('feedback_summary_required')
        existing=self.db.execute('SELECT id FROM feedback WHERE app=? AND sender=? AND household=? AND source_message_id=?',
                                 (*self.scope(identity),source_message_id)).fetchone()
        if existing:
            return existing['id']
        feedback_id=uuid.uuid4().hex
        with self.transaction():
            self.db.execute('INSERT INTO feedback VALUES(?,?,?,?,?,?,?,?,?)',
                            (feedback_id,episode_id,*self.scope(identity),summary.strip(),source_message_id,time.time(),'recorded'))
        return feedback_id

    def feedback_records(self, identity, episode_id=None):
        sql='SELECT * FROM feedback WHERE app=? AND sender=? AND household=?'
        params=self.scope(identity)
        if episode_id:
            sql+=' AND episode_id=?';params=(*params,episode_id)
        return [dict(row) for row in self.db.execute(sql+' ORDER BY created_at',params)]

    def delete_feedback(self, identity, feedback_id):
        with self.transaction():
            changed=self.db.execute('UPDATE feedback SET summary=NULL,state=? WHERE id=? AND app=? AND sender=? AND household=? AND state!=?',
                                     ('deleted',feedback_id,*self.scope(identity),'deleted')).rowcount
            if changed:
                self.db.execute('UPDATE revision_candidates SET proposed_json=?,state=? WHERE feedback_id=? AND app=? AND sender=? AND household=?',
                                ('{}','discarded',feedback_id,*self.scope(identity)))
            return changed>0

    def revision_candidate(self, identity, memory_id, feedback_id, proposed, source_message_id):
        if memory_id is not None and (not self.history(identity,memory_id) or self.history(identity,memory_id)[-1]['state']!='current'):
            raise ValueError('current_owned_memory_required')
        feedback=next((row for row in self.feedback_records(identity) if row['id']==feedback_id and row['state']=='recorded'),None)
        if not feedback:
            raise ValueError('owned_feedback_required')
        if not isinstance(proposed,dict) or set(proposed)-{'kind','key','value','starts_at','expires_at','constraint'}:
            raise ValueError('personal_revision_fields_only')
        candidate_id=uuid.uuid4().hex
        with self.transaction():
            self.db.execute('INSERT INTO revision_candidates VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                            (candidate_id,*self.scope(identity),memory_id,feedback_id,canonical(proposed),source_message_id,
                             time.time(),time.time()+600,'pending' if proposed else 'needs_clarification'))
        return candidate_id

    def revision_candidates(self, identity):
        result=[]
        for row in self.db.execute('SELECT * FROM revision_candidates WHERE app=? AND sender=? AND household=? ORDER BY created_at',self.scope(identity)):
            item=dict(row);item['proposed']=json.loads(item.pop('proposed_json'))
            if item['state']=='pending' and item['expires_at']<time.time():
                item['state']='expired'
            result.append(item)
        return result

    def confirmed_revision_candidate(self, identity, candidate_id):
        item=next((row for row in self.revision_candidates(identity) if row['id']==candidate_id),None)
        if not item or item['state']!='pending':
            raise ValueError('revision_candidate_not_pending')
        feedback=next((row for row in self.feedback_records(identity) if row['id']==item['feedback_id']),None)
        if not feedback or feedback['state']!='recorded':
            raise ValueError('revision_feedback_removed')
        self.db.execute('UPDATE revision_candidates SET state=? WHERE id=?',('confirmed',candidate_id))
        return item
