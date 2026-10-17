"""ER-CH-contract-1 client; all state changes stay in the local simulator core."""
import hashlib
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class RuntimeUnavailable(RuntimeError):
    pass


class RuntimeOutcomeUnknown(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


class RuntimeAPI:
    def __init__(self, base='http://127.0.0.1:19389'):
        parsed=urlsplit(base)
        if parsed.scheme!='http' or parsed.hostname not in {'127.0.0.1','localhost'} or parsed.path or parsed.query or parsed.username:
            raise ValueError('runtime_must_be_fixed_loopback')
        self.base=base
        self.opener=build_opener(ProxyHandler({}),NoRedirect())

    @staticmethod
    def owner(identity):
        if identity['household']!='site_1':
            raise ValueError('unsupported_bound_household')
        scope='\x1f'.join((identity['app'],identity['sender'],identity['household']))
        return {'ownerKey':hashlib.sha256(scope.encode()).hexdigest(),'householdId':identity['household']}

    def request(self, path, data=None, executing=False, expected_environment='local_debug1_sim'):
        headers={'Content-Type':'application/json'}
        if path!='/health':
            try:
                config=json.loads((Path(os.environ['LOCALAPPDATA'])/'AnkerEnergyRuntime'/'api.json').read_text(encoding='utf-8'))
                if config.get('url')!=self.base or not isinstance(config.get('token'),str) or not config['token']:
                    raise ValueError('invalid_private_runtime_configuration')
                headers['X-Energy-Runtime-Key']=config['token']
            except (OSError,ValueError,KeyError):
                raise RuntimeUnavailable('local_runtime_not_configured') from None
        request=Request(self.base+path,data=json.dumps(data,ensure_ascii=False,allow_nan=False).encode() if data is not None else None,
                        headers=headers)
        try:
            with self.opener.open(request,timeout=35 if executing else 12) as response:
                result=json.loads(response.read(2_000_000))
        except HTTPError as error:
            try:
                body=json.loads(error.read(4000))
                code=body.get('error',{})
                code=code.get('code') if isinstance(code,dict) else code
            except (ValueError,AttributeError):
                code='runtime_rejected'
            if executing and (error.code>=500 or 'unknown' in str(code)):
                raise RuntimeOutcomeUnknown('execution_result_unknown_do_not_retry') from None
            raise RuntimeUnavailable(code or 'runtime_rejected') from None
        except (URLError,TimeoutError,OSError,ValueError):
            if executing:
                raise RuntimeOutcomeUnknown('execution_result_unknown_do_not_retry') from None
            raise RuntimeUnavailable('runtime_unavailable') from None
        if not isinstance(result,dict):
            if executing:
                raise RuntimeOutcomeUnknown('invalid_execution_receipt')
            raise RuntimeUnavailable('invalid_runtime_response')
        if result.get('environment')!=expected_environment and not (expected_environment=='local_debug1_sim' and result.get('schemaVersion')=='assistant-decision-v1' and result.get('origin',{}).get('environment')==expected_environment):
            raise RuntimeUnavailable('runtime_environment_mismatch')
        return result

    def environment_snapshot(self):
        result=self.request('/environment/snapshot',expected_environment='release_official_ha_unverified')
        if (result.get('schemaVersion')!='environment-pipeline-v1' or result.get('householdId')!='site_1'
                or result.get('status') not in {'disabled','ready','partial','unavailable'}
                or result.get('control',{}).get('enabled') is not False):
            raise RuntimeUnavailable('release_response_invalid')
        return result

    def health(self):
        result=self.request('/health')
        if result.get('status')!='ready':
            raise RuntimeUnavailable('local_sim_not_ready')
        return result

    def snapshot(self):
        result=self.request('/snapshot')
        window=result.get('targetWindow',{})
        if window.get('timeBasis')!='device_local_naive' or window.get('timezone') is not None:
            raise RuntimeUnavailable('device_time_basis_mismatch')
        return result

    def proposal(self, identity, effective_constraints, request_id, preview_id=None):
        body={'owner':self.owner(identity),'effectiveConstraints':effective_constraints,'requestId':request_id}
        if preview_id:body['previewId']=preview_id
        return self.request('/proposal',body)

    def preview(self, identity, effective_constraints, minimum_percent, replace_id, request_id):
        return self.request('/preview',{'owner':self.owner(identity),'effectiveConstraints':effective_constraints,
                           'candidate':{'minimumPercent':minimum_percent,'replaceMemoryId':replace_id},'requestId':request_id})

    def decision(self, identity, episode_id=None, preview_id=None):
        query={'ownerKey':self.owner(identity)['ownerKey']}
        query['episodeId' if episode_id else 'previewId']=episode_id or preview_id
        return self.request('/decision?'+urlencode(query))

    def confirm(self, identity, episode_id, proposal_id, confirmation_id):
        return self.request('/confirm',{'owner':self.owner(identity),'episodeId':episode_id,
                                       'proposalId':proposal_id,'confirmationId':confirmation_id},executing=True)

    def episode(self, identity, episode_id):
        return self.request('/episode?'+urlencode({'episodeId':episode_id,'ownerKey':self.owner(identity)['ownerKey']}))

    def reconcile(self, identity, episode_id):
        return self.request('/reconcile',{'owner':self.owner(identity),'episodeId':episode_id})
