"""Run from the main project: python -m channels.feishu --help."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import getpass
import json
import logging
import os
import threading
import time
import uuid
import warnings
import re
import hashlib
from pathlib import Path

from assistant.energy_api import EnergyAPI
from assistant.service import Service
from assistant.store import Store
from .credentials import load, runtime_dir, save


def plain_text(content, message_type):
    payload = json.loads(content)
    if message_type == 'text':
        return payload['text']
    if message_type != 'post' or not isinstance(payload,dict):
        return None
    if 'content' not in payload:
        payload = payload.get('zh_cn') or payload.get('en_us') or {}
    lines=[]
    if payload.get('title'):
        lines.append(payload['title'])
    for row in payload.get('content',[]):
        chunks=[]
        for span in row:
            if span.get('tag') not in {'text','md','code_block'} or not isinstance(span.get('text'),str):
                return None
            chunks.append(span['text'])
        lines.append(''.join(chunks))
    return '\n'.join(lines)


def normalize(raw, app_id):
    try:
        if raw['header']['app_id'] != app_id or raw['header']['event_type'] != 'im.message.receive_v1':
            return None
        event, message = raw['event'], raw['event']['message']
        sender = event['sender']
        if sender['sender_type'] != 'user' or message['chat_type'] != 'p2p' or message['message_type'] not in {'text','post'}:
            return None
        result = {'sender':sender['sender_id']['open_id'], 'message':message['message_id'],
                  'chat':message['chat_id'], 'text':plain_text(message['content'],message['message_type'])}
        if not (result['sender'].startswith('ou_') and result['message'].startswith('om_') and result['chat'].startswith('oc_')
                and isinstance(result['text'],str) and 0 < len(result['text']) <= 2000):
            return None
        return result
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def notification_delivery(response):
    """A transport error or missing provider receipt never proves delivery."""
    status=getattr(getattr(response,'raw',None),'status_code',None)
    if isinstance(status,int) and not isinstance(status,bool):
        if 400<=status<500:return {'state':'failed'}
        if 200<=status<300 and response.success():
            message=getattr(getattr(response,'data',None),'message_id',None)
            if isinstance(message,str) and 0<len(message)<=256:return {'state':'sent','messageId':message}
    return {'state':'unknown'}


class Worker:
    def __init__(self, app_id, store, service, send):
        self.app_id, self.store, self.service, self.send = app_id,store,service,send

    def receive(self, event):
        work = self.accept(event)
        if work:
            self.process(work)

    def accept(self, event):
        outbound = str(uuid.uuid5(uuid.NAMESPACE_URL, self.app_id + ':' + event['message']))
        if self.store.claim(self.app_id,event['message'],outbound,event['sender'],event['text']):
            return {'message':event['message'],'sender':event['sender'],'text':event['text'],
                    'outbound_uuid':outbound,'reply':None}
        return None

    def process(self, work):
        message = work['message']
        if not work.get('reply'):
            self.store.state(self.app_id,message,'processing')
            try:
                reply = self.service.handle(self.app_id,work['sender'],message,work['text'])
            except Exception:
                reply = '这次处理未完成，结果尚未核实。请用“状态”或“仿真进度”只读核对；不会自动重发控制。'
            self.store.save_reply(self.app_id,message,reply)
        else:
            reply = work['reply']
        self.store.state(self.app_id,message,'sending')
        try:
            success = self.send(message,reply,work['outbound_uuid'])
        except Exception:
            success = False
        # A failed reply can have reached Feishu before timeout; do not automatically repeat it.
        self.store.state(self.app_id,message,'sent' if success else 'unknown')

    def recover(self):
        for work in self.store.recover(self.app_id):
            self.process(work)


def main():
    parser = argparse.ArgumentParser(description='Local Feishu household assistant; explicitly confirmed local simulation only')
    parser.add_argument('--configure', action='store_true')
    parser.add_argument('--pair', action='store_true')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--check-auth', action='store_true')
    parser.add_argument('--energy-api', default='http://127.0.0.1:19388')
    parser.add_argument('--model-api', default='http://127.0.0.1:4178')
    args = parser.parse_args()
    if args.configure:
        app_id = input('App ID: ').strip()
        secret = getpass.getpass('App secret (hidden): ').strip()
        if not app_id.startswith('cli_') or not secret:
            raise SystemExit('Invalid app credentials')
        save(app_id,secret)
        print('Encrypted user-bound credentials saved. No global CLI profiles changed.')
        return
    directory = runtime_dir()
    credentials = load()
    store = Store(directory / 'assistant.sqlite3')
    if args.pair:
        print(json.dumps({'pairingCode':store.pair_code(credentials['app_id']), 'expiresInSeconds':600,
                          'household':'site_1'},ensure_ascii=False))
        return
    status_path = directory / 'status.json'
    if args.status:
        print(status_path.read_text() if status_path.exists() else json.dumps({'running':False}))
        print(json.dumps({'messageStates':store.counts()}))
        return
    warnings.filterwarnings('ignore', message='pkg_resources is deprecated.*')
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody, CreateMessageRequest, CreateMessageRequestBody
    # SDK connection errors can contain signed URLs; never forward SDK diagnostics to stdout/log files.
    logging.getLogger('Lark').disabled = True
    logging.getLogger('lark_oapi').disabled = True
    app_id = credentials['app_id']
    client = lark.Client.builder().app_id(app_id).app_secret(credentials['app_secret']).timeout(12).log_level(lark.LogLevel.ERROR).build()
    if args.check_auth:
        from urllib.request import Request,urlopen
        try:
            request = Request('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
                              data=json.dumps(credentials).encode(),headers={'Content-Type':'application/json'})
            with urlopen(request,timeout=15) as response:
                result = json.load(response)
            print(json.dumps({'authenticated':result.get('code') == 0,'code':result.get('code')}))
        except Exception:
            print(json.dumps({'authenticated':False,'error':'auth_or_network_failed'}))
        return
    service = Service(store,EnergyAPI(args.energy_api,args.model_api))
    project = Path(__file__).resolve().parent.parent
    source_hashes = {str(path.relative_to(project)):hashlib.sha256(path.read_bytes()).hexdigest()
                     for folder in ('assistant','channels') for path in (project/folder).glob('*.py')}
    def send(message,text,outbound):
        request = ReplyMessageRequest.builder().message_id(message).request_body(
            ReplyMessageRequestBody.builder().msg_type('text').content(json.dumps({'text':text},ensure_ascii=False)).uuid(outbound).build()).build()
        response = client.im.v1.message.reply(request)
        return response.success()
    def send_owner(identity,text,outbound):
        from assistant.notifications import owner,zone
        if identity.get('app')!=app_id:return {'state':'failed'}
        binding=owner(store.db,identity)
        if zone(binding) is None or not binding['sender'].startswith('ou_'):
            return {'state':'failed'}
        request=CreateMessageRequest.builder().receive_id_type('open_id').request_body(
            CreateMessageRequestBody.builder().receive_id(binding['sender']).msg_type('text')
            .content(json.dumps({'text':text},ensure_ascii=False)).uuid(outbound).build()).build()
        response=client.im.v1.message.create(request)
        return notification_delivery(response)
    worker = Worker(app_id,store,service,send)
    inbox = Worker(app_id,Store(directory / 'assistant.sqlite3'),service,send)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='household')
    from assistant.web_gateway import WebGateway,serve_gateway
    web_gateway=WebGateway(app_id,store,service)
    web_server=serve_gateway(web_gateway,executor)
    executor.submit(worker.recover)
    def receive(data):
        event = normalize(json.loads(lark.JSON.marshal(data)),app_id)
        if event:
            work = inbox.accept(event)
            if work:
                executor.submit(worker.process,work)
    handler = lark.EventDispatcherHandler.builder('','').register_p2_im_message_receive_v1(receive).build()
    websocket = lark.ws.Client(app_id,credentials['app_secret'],event_handler=handler,log_level=lark.LogLevel.ERROR)
    web_gateway.transport_connected=lambda:getattr(websocket,'_conn',None) is not None
    def monitor():
        notification_future=None
        while True:
            store.prune_cache()
            connected = getattr(websocket,'_conn',None) is not None
            snapshot = {'pid':os.getpid(),'running':True,'connected':connected,'updatedAt':time.time(),
                        'energyApi':args.energy_api,'modelApi':args.model_api,'messageStates':store.counts(),
                        'sourceHashes':source_hashes,'webGateway':'http://127.0.0.1:19390'}
            temporary = directory / 'status.tmp'
            temporary.write_text(json.dumps(snapshot),encoding='utf-8')
            temporary.replace(status_path)
            if notification_future is None or notification_future.done():
                notification_future=executor.submit(service.notifications.tick,send_owner,app_id)
            time.sleep(5)
    threading.Thread(target=monitor,daemon=True).start()
    try:
        websocket.start()
    except Exception:
        status_path.write_text(json.dumps({'running':False,'error':'connection_failed','pid':os.getpid()}),encoding='utf-8')
        raise SystemExit('Feishu connection failed; check app capability, event subscription, permissions and network.') from None


if __name__ == '__main__':
    main()
