"""Single-owner loopback web transport sharing the Feishu Service and executor."""
from concurrent.futures import TimeoutError as FutureTimeout
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from importlib import import_module
import hashlib
import json
import math
import re
import secrets
import threading
import time
from urllib.parse import parse_qs,urlsplit
import uuid

from .runtime_api import RuntimeUnavailable
from .service import normalize_command
from .decision_view import DecisionReader,ReadError
from .notifications import read_notifications,NeedsTimezone

ORIGINS={'http://127.0.0.1:19388','http://localhost:19388','http://127.0.0.1:5173','http://localhost:5173'}
COOKIE='anker_assistant_session'


class GatewayError(ValueError):
    def __init__(self,code,status=400): self.code,self.status=code,status


class WebGateway:
    def __init__(self, app_id, store, service):
        self.app_id,self.store,self.service=app_id,store,service
        self.transport_connected=lambda:False
        self.reader=DecisionReader(app_id,store.path,service.runtime.api)
        self.store.db.executescript('''
        CREATE TABLE IF NOT EXISTS web_sessions(digest TEXT PRIMARY KEY,app TEXT,sender TEXT,household TEXT,
          expires REAL,seen_token TEXT,seen_proposal TEXT);
        CREATE TABLE IF NOT EXISTS web_requests(owner TEXT,request_id TEXT,state TEXT,result TEXT,created REAL,episode_id TEXT,
          PRIMARY KEY(owner,request_id));
        ''')
        with self.store.db:
            self.store.db.execute('UPDATE web_requests SET state=? WHERE state=?',('unknown','processing'))

    def owner(self):
        rows=self.store.db.execute('SELECT app,sender,household FROM bindings WHERE app=? AND household=?',(self.app_id,'site_1')).fetchall()
        if len(rows)!=1:
            raise GatewayError('owner_unbound' if not rows else 'owner_ambiguous',409)
        return dict(rows[0])

    def session(self, cookie, create=False):
        owner=self.owner()
        token=None
        if cookie:
            try:
                parsed=SimpleCookie();parsed.load(cookie)
                token=parsed[COOKIE].value if COOKIE in parsed else None
            except Exception:
                token=None
        digest=hashlib.sha256(token.encode()).hexdigest() if token else None
        row=self.store.db.execute('SELECT * FROM web_sessions WHERE digest=?',(digest,)).fetchone() if digest else None
        if row and (row['expires']<time.time() or (row['app'],row['sender'],row['household'])!=self.store.scope(owner)):
            row=None
        new_token=None
        if not row and create:
            new_token=secrets.token_urlsafe(32)
            digest=hashlib.sha256(new_token.encode()).hexdigest()
            with self.store.db:
                self.store.db.execute('INSERT INTO web_sessions VALUES(?,?,?,?,?,NULL,NULL)',(digest,*self.store.scope(owner),time.time()+43200))
            row=self.store.db.execute('SELECT * FROM web_sessions WHERE digest=?',(digest,)).fetchone()
        if not row:
            raise GatewayError('session_required',401)
        return owner,dict(row),new_token

    def pending(self, owner, session):
        record=self.store.pending_record(owner)
        if not record:
            return None
        data=record['data'];action=data['action']
        kind={'memory':'revision_candidate' if data.get('revisionCandidateId') else 'memory','forget':'forget',
              'feedback':'feedback','plan':'sample_plan','runtime_plan':'simulation_plan','clarify':'clarification'}.get(action)
        if not kind:
            return None
        summary=data.get('summary') or (f"{data.get('kind','资料')}：{data.get('key','')}＝{data.get('value','')}" if action=='memory' else
                                      '清理个人事实、活动、习惯与偏好' if action=='forget' else '固定八小时样例，未下发设备')
        choices=data.get('choiceDetails') or [{'id':choice,'label':'选项 '+str(int(choice)+1),'line1':''} for choice in data.get('choices',[])]
        can_confirm=not choices
        if action=='clarify':can_confirm=False
        if data.get('revisionCandidateId'):
            candidate=next((row for row in self.service.memory_v2.revision_candidates(owner) if row['id']==data['revisionCandidateId']),None)
            can_confirm=bool(candidate and candidate['state']=='pending')
        if action=='runtime_plan':
            episode=self.service.memory_v2.episode(owner,data['episodeId'])
            can_confirm=bool(episode and episode['state']=='proposed' and data['expiresAt']>time.time())
        result={'kind':kind,'token':record['token'],'expiresAt':min(record['expiresAt'],data.get('expiresAt',record['expiresAt'])),
                'summary':summary,'canConfirm':can_confirm,'confirmAction':'confirm_pending' if can_confirm else None,
                'choices':[{'id':str(choice['id']),'label':choice['label'],'description':choice.get('line1','')} for choice in choices]}
        for key in ('episodeId','proposalId','previewId','candidateDigest','execution','constraint','feedbackId','revisionCandidateId','consumedMemoryIds','memoryVersion'):
            if key in data: result[key]=data[key]
        with self.store.db:
            self.store.db.execute('UPDATE web_sessions SET seen_token=?,seen_proposal=? WHERE digest=?',
                                  (record['token'],data.get('proposalId'),session['digest']))
        return result

    def result(self, owner, session, **fields):
        return {'schemaVersion':'assistant-web-v1','ownerLabel':'家庭1','memoryVersion':self.service.memory_v2.version(owner),
                'pending':self.pending(owner,session),**fields}

    def translate(self, owner, session, body):
        if set(body)=={'requestId','text'} and isinstance(body['text'],str) and 0<len(body['text'])<=2000:
            normalized=normalize_command(body['text'])
            confirm=re.fullmatch(r'(?:执行|确认|记下|选\d+) ([A-Fa-f0-9]{6})',normalized)
            if confirm:
                pending=self.store.pending_record(owner)
                if not pending or session['seen_token']!=confirm.group(1).upper() or pending['token']!=session['seen_token']:
                    raise GatewayError('pending_not_shown_or_stale',409)
                if session['seen_proposal']!=pending['data'].get('proposalId'):
                    raise GatewayError('proposal_not_shown',409)
            return normalized
        if set(body)!={'requestId','action'} or not isinstance(body['action'],dict):
            raise GatewayError('invalid_message_fields')
        action=body['action'];kind=action.get('type')
        if kind in {'confirm_pending','choose_pending'}:
            expected={'type','token'} if kind=='confirm_pending' else {'type','token','choiceId'}
            if set(action)!=expected:
                raise GatewayError('invalid_action_fields')
            pending=self.store.pending_record(owner)
            if not pending or action['token']!=pending['token'] or session['seen_token']!=pending['token']:
                raise GatewayError('pending_not_shown_or_stale',409)
            data=pending['data']
            if session['seen_proposal']!=data.get('proposalId'):
                raise GatewayError('proposal_not_shown',409)
            if kind=='choose_pending':
                if data['action']!='plan' or str(action['choiceId']) not in data.get('choices',[]):
                    raise GatewayError('choice_mismatch',409)
                return '选'+str(int(action['choiceId'])+1)+' '+pending['token']
            command={'memory':'记下','forget':'记下','feedback':'记下','plan':'确认','runtime_plan':'执行'}.get(data['action'])
            if not command:
                raise GatewayError('pending_not_confirmable',409)
            return command+' '+pending['token']
        if kind=='delete_memory' and set(action)=={'type','memoryId'}:
            if not isinstance(action['memoryId'],str):raise GatewayError('invalid_memory_id')
            return '删除 '+action['memoryId']
        if kind=='feedback' and set(action)=={'type','episodeId','rating','reasonText'}:
            if action['rating'] not in {'suitable','unsuitable'} or not isinstance(action['reasonText'],str):
                raise GatewayError('invalid_feedback')
            return '反馈 '+str(action['episodeId'])+' '+('合适' if action['rating']=='suitable' else '不合适')+'：'+action['reasonText']
        raise GatewayError('unsupported_action')

    def message(self, owner, session, body):
        request_id=body.get('requestId') if isinstance(body,dict) else None
        try:
            if str(uuid.UUID(request_id))!=request_id: raise ValueError()
        except (ValueError,TypeError,AttributeError):
            raise GatewayError('invalid_request_id')
        owner_key=self.service.runtime.api.owner(owner)['ownerKey']
        prior=self.store.db.execute('SELECT * FROM web_requests WHERE owner=? AND request_id=?',(owner_key,request_id)).fetchone()
        if prior:
            return json.loads(prior['result']) if prior['result'] else self.old_request(owner,session,request_id,prior['state'],prior['episode_id'])
        text=self.translate(owner,session,body)
        with self.store.db:
            self.store.db.execute('INSERT INTO web_requests VALUES(?,?,?,?,?,NULL)',(owner_key,request_id,'processing',None,time.time()))
        message_id='web_'+hashlib.sha256((owner_key+':'+request_id).encode()).hexdigest()
        try:
            reply=self.service.handle(owner['app'],owner['sender'],message_id,text,request_id=request_id)
            metadata=self.service.last_result or {}
            result=self.result(owner,session,requestId=request_id,status='completed',reply={'text':reply,'kind':metadata.get('kind','message')})
            if metadata.get('episodeId'):
                result['episode']=self.public_episode(owner,metadata['episodeId'])
            if metadata.get('previewId'):result['previewId']=metadata['previewId']
            with self.store.db:
                self.store.db.execute('UPDATE web_requests SET state=?,result=?,episode_id=? WHERE owner=? AND request_id=?',
                                      ('completed',json.dumps(result,ensure_ascii=False),metadata.get('episodeId'),owner_key,request_id))
            return result
        except Exception:
            with self.store.db:self.store.db.execute('UPDATE web_requests SET state=? WHERE owner=? AND request_id=?',('unknown',owner_key,request_id))
            return self.result(owner,session,requestId=request_id,status='unknown',reply={'text':'这次请求结果尚不明确，请查原请求状态，不要重复执行。','kind':'unknown'})

    def old_request(self, owner, session, request_id, state, episode_id=None):
        expired=state=='completed'
        result=self.result(owner,session,requestId=request_id,status='completed' if expired else 'unknown',retryable=False,cacheExpired=expired,
                           reply={'text':'原请求已完成，但短期回复缓存已过期。请查看资料或安排记录核对，不会重新执行。' if expired else
                                        '原请求结果尚待核对，请查看资料或安排记录；不要重发执行。',
                                  'kind':'completed_receipt' if expired else 'unknown'})
        if episode_id:
            result['episode']=self.public_episode(owner,episode_id)
        return result

    def public_episode(self, owner, episode_id):
        episode=self.service.memory_v2.episode(owner,episode_id)
        if not episode:raise GatewayError('episode_not_found',404)
        allowed={'proposalId','confirmationId','requestId','consumedMemoryIds','unsupportedMemoryIds','rejectedMemoryIds',
                 'constraintsVersion','environment','reasonCode','sourceSlot','targetKw','evidenceKind','readbackMatches',
                 'observedBatteryKw','settlementAvailable','costEur','importKwh','exportKwh','success'}
        events=[{'sequence':event['sequence'],'phase':event['phase'],'createdAt':event['createdAt'],
                 'evidence':{key:value for key,value in event['evidence'].items() if key in allowed}}
                for event in episode['events']]
        return {'episodeId':episode['id'],'stage':episode['state'],'proposalId':episode['proposal_id'],
                'createdAt':episode['created_at'],'updatedAt':episode['updated_at'],'memoryVersion':episode['memory_version'],
                'constraintsVersion':episode['constraints_version'],'usedMemoryIds':episode['used_memory_ids'],
                'consumedMemoryIds':episode['consumed_memory_ids'],'events':events}

    def status(self, owner, session):
        home=None;runtime={'status':'unavailable','forecastStatus':'not_checked','executionStatus':'not_requested','reasonCode':None}
        try:
            health=self.service.runtime.api.health()
            home=self.service.runtime.home(owner)
            runtime['status']='snapshot_available'
        except (RuntimeUnavailable,ValueError,KeyError,TypeError) as error:
            runtime['reasonCode']=str(error)[:160]
        return self.result(owner,session,status='ready',home=home,runtime=runtime,channelConnected=self.transport_connected())

    def dispatch(self, method, path, cookie, body=None):
        parsed=urlsplit(path)
        if parsed.path in {'/api/assistant/notifications','/api/assistant/notification-settings'}:
            return self.notification_dispatch(method,path,cookie,body)
        if parsed.path=='/api/assistant/environment':
            if method!='GET':return 405,{'error':{'code':'method_not_allowed'}},None
            return self.environment_dispatch(path,cookie)
        if method=='GET' and parsed.path in {'/api/assistant/decision','/api/assistant/feed'}:
            return self.read_dispatch(path,cookie)
        try:
            with self.store.db:
                self.store.db.execute('UPDATE web_requests SET result=NULL WHERE result IS NOT NULL AND created<?',(time.time()-600,))
            owner,session,new_token=self.session(cookie,create=method=='GET' and parsed.path=='/api/assistant/session')
            if parsed.path=='/api/assistant/session' and method=='GET':
                result=self.result(owner,session,status='ready')
            elif parsed.path=='/api/assistant/status' and method=='GET':result=self.status(owner,session)
            elif parsed.path=='/api/assistant/messages' and method=='POST':result=self.message(owner,session,body)
            elif parsed.path=='/api/assistant/messages' and method=='GET':
                request_id=parse_qs(parsed.query).get('requestId',[''])[0]
                owner_key=self.service.runtime.api.owner(owner)['ownerKey']
                row=self.store.db.execute('SELECT * FROM web_requests WHERE owner=? AND request_id=?',(owner_key,request_id)).fetchone()
                if not row:raise GatewayError('request_not_found',404)
                result=json.loads(row['result']) if row['result'] else self.old_request(owner,session,request_id,row['state'],row['episode_id'])
            elif parsed.path=='/api/assistant/memories' and method=='GET':
                items=[]
                for record in self.store.memories(owner):
                    history=self.service.memory_v2.history(owner,record['id']);latest=history[-1]
                    items.append({'id':record['id'],'kind':record['kind'],'key':record['key'],'value':record['value'],
                                  'startsAt':record['starts_at'],'expiresAt':record['expires_at'],'updatedAt':record['updated_at'],
                                  'revision':latest['revision'],'status':latest['state'],
                                  'machineConstraint':json.loads(latest['constraint_json']) if latest['constraint_json'] else None})
                result=self.result(owner,session,status='ready',items=items)
            elif parsed.path=='/api/assistant/episodes' and method=='GET':
                ids=[row[0] for row in self.store.db.execute('SELECT id FROM episodes WHERE app=? AND sender=? AND household=? ORDER BY created_at DESC LIMIT 30',self.store.scope(owner))]
                result=self.result(owner,session,status='ready',items=[self.public_episode(owner,item) for item in ids])
            elif parsed.path.startswith('/api/assistant/episodes/') and method=='GET':
                result=self.result(owner,session,status='ready',episode=self.public_episode(owner,parsed.path.rsplit('/',1)[-1]))
            elif parsed.path=='/api/assistant/feedback' and method=='GET':
                records=self.service.memory_v2.feedback_records(owner)
                result=self.result(owner,session,status='ready',items=[{'id':row['id'],'episodeId':row['episode_id'],'summary':row['summary'],
                                              'createdAt':row['created_at'],'status':row['state']} for row in records],
                                   revisionCandidates=[{'id':row['id'],'feedbackId':row['feedback_id'],'memoryId':row['memory_id'],
                                                        'status':row['state'],'proposed':row['proposed']} for row in self.service.memory_v2.revision_candidates(owner)])
            else:raise GatewayError('route_not_found',404)
            return 200,result,new_token
        except GatewayError as error:
            return error.status,{'schemaVersion':'assistant-web-v1','status':{'owner_unbound':'unbound','owner_ambiguous':'ambiguous'}.get(error.code,'error'),
                                 'ownerLabel':None,'memoryVersion':None,'pending':None,'error':{'code':error.code}},None

    def read_dispatch(self,path,cookie):
        try:return 200,self.reader.read(path,cookie),None
        except ReadError as error:return error.status,{'error':{'code':error.code}},None
        except Exception:return 502,{'error':{'code':'decision_read_unavailable'}},None

    def environment_dispatch(self,path,cookie):
        """Separate provenance and read-only identity; never grants pending seen."""
        try:
            if urlsplit(path).query:raise ReadError('environment_query_not_allowed')
            with self.reader.connect() as db:self.reader.identity(db,cookie)
            return 200,self.service.runtime.api.environment_snapshot(),None
        except ReadError as error:
            return error.status,{'error':{'code':error.code}},None
        except (RuntimeUnavailable,AttributeError):
            parent=__package__.rsplit('.',1)[0]+'.' if '.' in __package__ else ''
            envelope=import_module(parent+'energy_runtime.release').envelope
            return 200,envelope('unavailable','release_core_unavailable'),None
        except Exception:return 502,{'error':{'code':'environment_read_unavailable'}},None

    def notification_dispatch(self,method,path,cookie,body=None):
        try:
            parsed=urlsplit(path)
            if parsed.query:raise ReadError('notification_query_not_allowed')
            is_read=parsed.path=='/api/assistant/notifications'
            if method!=('GET' if is_read else 'POST'):return 405,{'error':{'code':'method_not_allowed'}},None
            if not is_read and (not isinstance(body,dict) or set(body)!={'enabled'} or type(body['enabled']) is not bool):
                raise ReadError('invalid_notification_settings')
            with self.reader.connect() as db:
                who,_=self.reader.identity(db,cookie)
                if is_read:return 200,read_notifications(db,who),None
            try:
                result=self.service.notifications.set_enabled(who,body['enabled'])
            except NeedsTimezone:
                with self.reader.connect() as db:result=read_notifications(db,who)
                result['error']={'code':'needs_timezone'}
                return 409,result,None
            return 200,result,None
        except ReadError as error:return error.status,{'error':{'code':error.code}},None
        except Exception:return 503,{'error':{'code':'notifications_unavailable'}},None


def serve_gateway(gateway, executor, port=19390):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def respond(self,status,body,token=None):
            data=json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
            self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            if token:self.send_header('Set-Cookie',f'{COOKIE}={token}; Path=/api/assistant; HttpOnly; SameSite=Strict; Max-Age=43200')
            self.end_headers();self.wfile.write(data)
        def handle_request(self):
            origin=self.headers.get('Origin')
            bound_port=self.server.server_address[1]
            if self.headers.get('Host') not in {f'127.0.0.1:{bound_port}',f'localhost:{bound_port}'} or origin and origin not in ORIGINS or self.command=='POST' and origin not in ORIGINS:
                return self.respond(403,{'schemaVersion':'assistant-web-v1','error':{'code':'origin_or_host_rejected'}})
            body=None
            if self.command=='POST':
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if self.headers.get('Content-Type','').split(';')[0]!='application/json' or not 0<length<=16000:raise ValueError()
                    body=json.loads(self.rfile.read(length))
                except (ValueError,UnicodeError):return self.respond(400,{'error':{'code':'invalid_json'}})
            if self.command=='GET' and urlsplit(self.path).path in {'/api/assistant/decision','/api/assistant/feed'}:
                status,result,token=gateway.read_dispatch(self.path,self.headers.get('Cookie'))
                return self.respond(status,result,token)
            if self.command=='GET' and urlsplit(self.path).path=='/api/assistant/environment':
                status,result,token=gateway.environment_dispatch(self.path,self.headers.get('Cookie'))
                return self.respond(status,result,token)
            if self.command=='GET' and urlsplit(self.path).path=='/api/assistant/notifications':
                status,result,token=gateway.notification_dispatch('GET',self.path,self.headers.get('Cookie'))
                return self.respond(status,result,token)
            future=executor.submit(gateway.dispatch,self.command,self.path,self.headers.get('Cookie'),body)
            try:
                status,result,token=future.result(timeout=50)
                self.respond(status,result,token)
            except FutureTimeout:self.respond(202,{'schemaVersion':'assistant-web-v1','status':'processing','requestId':body.get('requestId') if isinstance(body,dict) else None,
                                                   'reply':None,'pending':None,'memoryVersion':None,'ownerLabel':None,'retryable':False})
            except Exception:self.respond(500,{'schemaVersion':'assistant-web-v1','status':'unknown','error':{'code':'assistant_processing_unknown'}})
        do_GET=handle_request
        do_POST=handle_request
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    threading.Thread(target=server.serve_forever,daemon=True,name='assistant-web').start()
    return server
