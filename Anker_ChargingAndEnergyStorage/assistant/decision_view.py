"""Bounded, owner-derived read models. No shared writer connection or seen writes."""
import hashlib
from http.cookies import SimpleCookie,CookieError
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from contextlib import contextmanager
from urllib.parse import parse_qs,urlsplit

from .memory_v2 import MemoryV2,canonical
from .runtime_api import RuntimeUnavailable


class ReadError(ValueError):
    def __init__(self,code,status=400):self.code,self.status=code,status


def fields(value, names):
    if not isinstance(value,dict):return None
    return {key:value.get(key) for key in names.split()}


def observation(snapshot):
    if snapshot.get('observation'):
        return safe_observation(snapshot['observation'])
    raw=snapshot.get('ha',{}).get('telemetry',{}).get('site_1',{})
    ctl=snapshot.get('ha',{}).get('controls',{}).get('site_1',{})
    def kw(x):return x/1000 if isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) else None
    target=kw(ctl.get('power_w'))
    target=target if ctl.get('direction')=='discharge' else -target if ctl.get('direction')=='charge' and target is not None else 0 if target==0 else None
    return {'kind':'current_snapshot','lastKnown':True,'sourceSlot':snapshot['sourceSlot'],
            'measurementSourceSlot':snapshot.get('telemetryPhysicalAsOfSlot'),'socBoundarySlot':snapshot.get('socBoundarySlot'),
            'physicalInputsKind':snapshot.get('physicalInputsKind'),'observedAt':snapshot.get('capturedAt'),'paused':snapshot.get('paused'),
            'socPercent':raw.get('soc_pct'),'pvKw':kw(raw.get('solar_power_w')),'loadKw':kw(raw.get('home_load_w')),
            'batteryActualKw':kw(ctl.get('battery_power_w')),'batteryTargetKw':target,'gridImportKw':None,'gridExportKw':None,
            'prices':{'buyEurKwh':None,'sellEurKwh':None,'sourceSlot':snapshot['sourceSlot'],'kind':'simulator_source_slot'},
            'fieldSources':{'grid':None,'power':'official_ha'}}


def safe_observation(value):
    result=fields(value,'kind lastKnown sourceSlot measurementSourceSlot socBoundarySlot physicalInputsKind observedAt paused socPercent pvKw loadKw batteryActualKw batteryTargetKw gridImportKw gridExportKw')
    if result is not None:
        result['prices']=fields(value.get('prices'),'buyEurKwh sellEurKwh sourceSlot kind')
        result['fieldSources']=fields(value.get('fieldSources'),'grid power')
    return result


def safe_plan(value):
    if not isinstance(value,dict):return None
    result=fields(value,'status reserveEnergyKwh minimumPercent hypothetical expectedHorizonCostEur')
    result['executable']=False
    result['firstAction']=fields(value.get('firstAction'),'targetPowerW sourceSlot intervalMinutes intervalCount signConvention')
    result['points']=[fields(row,'targetSlot start end chargeKw dischargeKw importKw exportKw curtailKw energyEndKwh socEndPercent') for row in value.get('points',[])[:500]]
    return result


def safe_view(value):
    if value.get('schemaVersion')!='assistant-decision-v1':raise ReadError('invalid_decision_source',502)
    result=fields(value,'schemaVersion availability presentationState reasonCode episodeId proposalId previewId expiresAt')
    origin=value.get('origin')
    result['origin']=fields(origin,'environment policyId sourceSlot deviceTime timeBasis dtHours initialEnergyKwh socRoundingMarginPercent measurementSourceSlot socBoundarySlot physicalInputsKind sourceSnapshotDigest forecastDigest observedAt sourceValidation')
    if origin:
        result['origin']['targetWindow']=fields(origin.get('targetWindow'),'start end timeBasis timezone')
        result['origin']['physics']=fields(origin.get('physics'),'capacityKwh maxChargeKw maxDischargeKw maxGridImportKw maxGridExportKw effCharge effDischarge')
    result['observation']=safe_observation(value.get('observation'))
    summary=value.get('constraints');result['constraints']=None
    if summary:
        result['constraints']=fields(summary,'memoryVersion constraintsVersion reserveEnergyKwh minimumPercent consumedMemoryIds contextOnlyMemoryIds missingFields')
        result['constraints']['items']=[{**fields(row,'memoryId revision type startsAt expiresAt'),'value':fields(row.get('value'),'minimumPercent')} for row in summary.get('items',[])]
    forecast=value.get('forecast');result['forecast']=None
    if forecast:
        result['forecast']={'points':[fields(row,'targetSlot start end pvKw loadKw buyEurKwh sellEurKwh inputKind') for row in forecast.get('points',[])[:500]],
            'provenance':fields(forecast.get('provenance',{}),'pvModel loadModel pvCheckpointSha256 loadCheckpointSha256 endedHistoryFirstSlot endedHistoryLastSlot historyPoints physicalInputsKind telemetryPhysicalAsOfSlot socBoundarySlot weatherSource priceSource inputKind loadExogenousAlignment')}
    result['plan']=safe_plan(value.get('plan'))
    preview=value.get('preview');result['preview']=None
    if preview:
        result['preview']=fields(preview,'status parentEpisodeId candidateDigest sourceSnapshotDigest forecastDigest sameOrigin requestedMinimumPercent replaceMemoryId')
        result['preview'].update(baseline=safe_plan(preview.get('baseline')),candidate=safe_plan(preview.get('candidate')))
    execution=value.get('execution',{})
    result['execution']=fields(execution,'phase requestId executedSlot observedBoundarySlot readbackMatches settlementAvailable failureCode')
    result['execution']['events']=[fields(row,'sequence phase eventAt evidenceKind') for row in execution.get('events',[])]
    result['actual']=fields(value.get('actual'),'executedSlot observedBoundarySlot start end pvKw loadKw batteryActualKw gridImportKw gridExportKw socStartPercent socEndPercent intervalCostEur reserveMet readbackMatches settlementAvailable evidenceRef')
    result['savings']='not_evaluated'
    return result


def empty_view():
    return {'schemaVersion':'assistant-decision-v1','availability':'empty','presentationState':'empty','reasonCode':'no_decision',
            'episodeId':None,'proposalId':None,'previewId':None,'expiresAt':None,'origin':None,'observation':None,'constraints':None,
            'currentConstraints':None,'pendingRef':None,'forecast':None,'plan':None,'preview':None,
            'execution':{'phase':'empty','events':[],'requestId':None,'executedSlot':None,'observedBoundarySlot':None,
                         'readbackMatches':None,'settlementAvailable':False,'failureCode':None},'actual':None,'savings':'not_evaluated'}


class DecisionReader:
    def __init__(self, app_id, path, api):self.app_id,self.path,self.api=app_id,Path(path),api

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)
        db.row_factory=sqlite3.Row
        try:yield db
        finally:db.close()

    def identity(self,db,cookie):
        rows=db.execute('SELECT app,sender,household FROM bindings WHERE app=? AND household=?',(self.app_id,'site_1')).fetchall()
        if len(rows)!=1:raise ReadError('owner_unbound' if not rows else 'owner_ambiguous',409)
        owner=dict(rows[0]);scope=tuple(owner[k] for k in ('app','sender','household'))
        try:
            parsed=SimpleCookie();parsed.load(cookie or '')
            token=parsed['anker_assistant_session'].value
        except (KeyError,ValueError,CookieError):raise ReadError('session_required',401) from None
        session=db.execute('SELECT * FROM web_sessions WHERE digest=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not session or session['expires']<=time.time() or tuple(session[k] for k in ('app','sender','household'))!=scope:
            raise ReadError('session_required',401)
        return owner,scope

    @staticmethod
    def selection(db,scope):
        row=db.execute('SELECT * FROM decision_selection WHERE app=? AND sender=? AND household=?',scope).fetchone()
        return dict(row) if row else None

    def read(self,path,cookie):
        query=parse_qs(urlsplit(path).query,keep_blank_values=True)
        if any(len(values)!=1 for values in query.values()):raise ReadError('duplicate_query_fields')
        if any(not values[0] for values in query.values()):raise ReadError('empty_query_field')
        with self.connect() as db:
            owner,scope=self.identity(db,cookie)
            if urlsplit(path).path.endswith('/feed'):return self.feed(db,scope,query)
            if set(query)-{'episodeId'}:raise ReadError('invalid_decision_query')
            selected=self.selection(db,scope);version=MemoryV2(db).version(owner)
            explicit=query.get('episodeId',[None])[0]
            if explicit and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',explicit):raise ReadError('invalid_episode_id')
            if explicit and not db.execute('SELECT 1 FROM episodes WHERE id=? AND app=? AND sender=? AND household=?',(explicit,*scope)).fetchone():
                raise ReadError('episode_not_found',404)
            episode=explicit or (selected or {}).get('episode_id');preview=None if explicit else (selected or {}).get('preview_id')
        try:
            if episode or preview:
                view=safe_view(self.api.decision(owner,episode_id=episode,preview_id=None if episode else preview))
            else:
                view=empty_view();view['observation']=observation(self.api.snapshot())
        except RuntimeUnavailable:
            view=empty_view();view.update(availability='unavailable',reasonCode='core_unavailable')
        with self.connect() as db:
            current_owner,scope2=self.identity(db,cookie)
            current_version=MemoryV2(db).version(current_owner)
            pending=db.execute('SELECT * FROM pending WHERE app=? AND sender=? AND household=?',scope).fetchone()
            pending=json.loads(pending['data']) if pending and pending['expires']>time.time() else None
            view['pendingRef']=None if not pending else {'kind':pending['action'],'episodeId':pending.get('episodeId'),
                 'proposalId':pending.get('proposalId'),'previewId':pending.get('previewId'),'needsSessionRefresh':True}
            window=(view.get('origin') or {}).get('targetWindow')
            view['currentConstraints']=None
            if window:
                effective=MemoryV2(db).effective_constraints(owner,window['start'],window['end'],window['timeBasis'])
                view['currentConstraints']={'memoryVersion':current_version,'constraintsVersion':effective['constraintsVersion'],
                    'targetWindow':effective['targetWindow'],'items':effective['constraints'],
                    'contextOnlyMemoryIds':effective['contextOnlyMemoryIds'],'missingFields':effective['missingFields']}
            alive={row[0] for row in db.execute('SELECT id FROM memories WHERE app=? AND sender=? AND household=?',scope)}
            if view.get('constraints'):view['constraints']['items']=[item for item in view['constraints']['items'] if item['memoryId'] in alive]
            if scope!=scope2 or version!=current_version or selected!=self.selection(db,scope):
                view.update(availability='stale',reasonCode='view_changed_during_read')
            elif selected and selected['memory_version']!=current_version and view['execution']['phase'] not in {'outcome','settled'}:
                view.update(availability='stale',reasonCode='memory_changed')
            elif not explicit and preview and not episode and (not pending or pending.get('previewId')!=preview or pending.get('candidateDigest')!=selected['candidate_digest']):
                view.update(availability='stale',reasonCode='candidate_replaced')
        view['viewVersion']=hashlib.sha256(canonical(view).encode()).hexdigest()
        return view

    def feed(self,db,scope,query):
        if set(query)-{'after'}:raise ReadError('invalid_feed_query')
        after=query.get('after',[None])[0];threshold=time.time()-600;reset=False
        row=db.execute('SELECT sequence FROM interaction_feed WHERE id=? AND app=? AND sender=? AND household=? AND created_at>?',
                       (after,*scope,threshold)).fetchone() if after else None
        if after and not row:reset=True
        if row:
            records=db.execute('SELECT * FROM interaction_feed WHERE app=? AND sender=? AND household=? AND created_at>? AND sequence>? ORDER BY sequence LIMIT 51',(*scope,threshold,row[0])).fetchall()
        else:
            records=list(reversed(db.execute('SELECT * FROM interaction_feed WHERE app=? AND sender=? AND household=? AND created_at>? ORDER BY sequence DESC LIMIT 50',(*scope,threshold)).fetchall()))
        more=len(records)>50;records=records[:50]
        return {'schemaVersion':'assistant-feed-v1','retentionSeconds':600,'reset':reset,'hasMore':more,
                'cursor':records[-1]['id'] if records else after if row else None,
                'items':[{'id':r['id'],'requestId':r['request_id'],'sourceChannel':r['source_channel'],'userText':r['user_text'],
                    'agentText':r['agent_text'],'createdAt':r['created_at'],'episodeId':r['episode_id'],'previewId':r['preview_id']} for r in records]}
