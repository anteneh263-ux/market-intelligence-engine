import copy
import pytest
pytest.importorskip('cryptography')
from test_phase6_anchor_validation import request as anchors, sign
from test_phase6_reservation_ledger import setup, inputs
from test_phase6_event_validation import case as event_case
from test_phase6_intake_binding import read
from test_phase6_bar_call import clock
from app.research.phase6_validation import canonical_hash as H
from app.research.phase6_event_journal import EventJournal
from app.research.phase6_object_store import ObjectWriter
from app.paper.session import diagnostic_strategy_v1 as strategy, shadow_reducer_v1 as reducer


def fixture(tmp_path,n=2,bound=False,warmup=2):
    ledger,root,evidence=setup(tmp_path);p,a=inputs(root,evidence)
    trust=anchors()['trust'];trust['not_after_utc_ms']=1000000
    pa=dict(schema_version='p6.diagnostic_params.v1',warmup_bars=warmup,targets=['0.25','0.75'])
    p.update(anchor_trust_hash=H(trust),params_hash=H(pa),warmup_bars=warmup,end_ms=300000,max_target='0.75');a['plan_hash']=H(p)
    reserved=ledger.reserve(p,a,reserved_utc_ms=2);ledger.close();r=reserved['reservation'];run=H(r)
    s=reducer.initial({k:p[k] for k in reducer.CONFIG_DEC+reducer.CONFIG_INT});ss=strategy.initial(pa)
    g=dict(schema_version='p6.genesis.v1',run_id=run,plan_hash=H(p),strategy_state_hash=H(ss),**{k:s[k] for k in 'cash units cost_basis cumulative_fees cumulative_realized_pnl equity peak_equity'.split()})
    receipts=[]
    def receipt(kind,subject,t):
        receipts.append(sign(dict(schema_version='p6.anchor_receipt.v1',run_id=run,anchor_seq=len(receipts)+1,subject_kind=kind,subject_hash=H(subject),previous_anchor_hash=H(receipts[-1]) if receipts else '0'*64,retained_utc_ms=t,service_identity=trust['service_identity'])))
    def envelope(now):
        return dict(trust=trust,receipts=copy.deepcopy(receipts),head=sign(dict(schema_version='p6.anchor_head.v1',run_id=run,anchor_seq=len(receipts),anchor_hash=H(receipts[-1]),service_identity=trust['service_identity'],nonce='a'*64,observed_utc_ms=now,retention_until_ms=900000)),challenge=dict(nonce='a'*64,issued_utc_ms=now-1),now_utc_ms=now)
    receipt('RESERVATION',r,3);receipt('GENESIS',g,4)
    start=dict(plan=p,authorization=a,reservation=r,genesis=g,registry_identity=r['registry_identity'],strategy_state_hash=H(ss),anchor=envelope(10))
    bundles=[];prior=H(g)
    for i in range(1,n+1):
        t=60000+i*60000+10;m=event_case()['manifest'];m.update(run_id=run,plan_hash=H(p),params_hash=H(pa),strategy_code_hash=p['strategy_code_hash'],segment_id=str(i)*64,prior_checkpoint_hash=prior,prior_anchor_hash=H(receipts[-1]),persisted_utc_ms=t)
        if bound:
            intake=intake_for(start,i);m['segment_id']=H(intake['segments'][-1])
        dest=tmp_path/('session'+str(i));dest.mkdir();w=ObjectWriter(str(dest),owner='test',run_id=run,max_bytes=1000000);j=EventJournal(w,m,utc_ms=t,monotonic_ns=t*1000000)
        j.append('BAR_ACCEPTED',dict(schema_version='p6.payload.bar_accepted.v1',segment_hash=m['segment_id'],receipt_hash=H(intake['receipts'][-1]) if bound else 'c'*64,bar_hash=intake['segments'][-1]['bar_hash'] if bound else 'd'*64,accepted_prefix_hash=H(intake['prefix']) if bound else 'e'*64),utc_ms=t+1,monotonic_ns=(t+1)*1000000)
        if bound:j.bind_intake(intake)
        j.run_diagnostic(p,pa,ss,segment_seq=i,clock=clock(t+2))
        o=dict(close_ms=t-10,close='100',received_utc_ms=t,received_monotonic_ns=t*1000000,clock_session_id='test',decision_utc_ms=t+3,decision_monotonic_ns=(t+3)*1000000)
        j.run_bar(s,o,clock=clock(t+6))
        if i>warmup:
            from test_phase6_quote_receipt_binding import extend,run as quote_run
            assert bound
            intake=extend(intake,t+10);j.bind_quote_receipts(intake);quote_run(j,intake)
        previous=j.snapshot()['last_event'];out=j.prepare_checkpoint(last_receipt_seq=len(intake['receipts']) if bound else i,clock=clock(t+14))
        c=read(w,out['commit_hash']);e=read(w,out['event_hash']);k=read(w,out['checkpoint_hash'])
        b=dict(manifest=m,checkpoint=k,checkpoint_commit=c,event=e,payload=read(w,e['payload_hash']),previous_event=previous,state=read(w,c['state_hash']),strategy_state=read(w,c['strategy_state_hash']),execution_commit=read(w,c['execution_commit_hash']),diagnostic_commit=read(w,c['diagnostic_commit_hash']));bundles.append(b);j.close()
        s=b['state'];ss=b['strategy_state'];prior=H(k);receipt('CHECKPOINT',k,t+16)
    return dict(start=start,checkpoints=bundles,anchor=envelope(t+18))


def verify(q):
    from app.research.phase6_checkpoint_anchor_validation import verify_checkpoint_anchors
    return verify_checkpoint_anchors(q)


def resign(q):
    rr=q['anchor']['receipts']
    for i,r in enumerate(rr):
        r['previous_anchor_hash']=H(rr[i-1]) if i else '0'*64;sign(r)
    q['anchor']['head']['anchor_hash']=H(rr[-1]);sign(q['anchor']['head'])


@pytest.mark.parametrize('n',[1,2])
def test_actual_local_checkpoints_and_signed_chain(tmp_path,n):
    q=fixture(tmp_path,n);before=copy.deepcopy(q);r=verify(q)
    assert r['binding_status']=='VALID',r
    assert r['checkpoint_count']==n and r['checkpoint_hash']==H(q['checkpoints'][-1]['checkpoint'])
    assert r['last_anchor_hash']==H(q['anchor']['receipts'][-1])
    assert not r['runtime_accepted'] and not r['independent_retention_verified'] and not r['full_execution_verified']
    assert q==before


@pytest.mark.parametrize('fault',['signature','wrong_subject','missing_receipt','extra_receipt','stale_head','offline','nonce','trust','retention','before_checkpoint','future_receipt','start_prefix','checkpoint_state','order','skip','extra_field'])
def test_refusals(tmp_path,fault):
    q=fixture(tmp_path)
    if fault=='signature':q['anchor']['receipts'][-1]['signature']='0'*128
    if fault=='wrong_subject':q['anchor']['receipts'][-1]['subject_hash']='f'*64;resign(q)
    if fault=='missing_receipt':q['anchor']['receipts'].pop()
    if fault=='extra_receipt':q['anchor']['receipts'].append(copy.deepcopy(q['anchor']['receipts'][-1]))
    if fault=='stale_head':q['anchor']['now_utc_ms']+=101
    if fault=='offline':q['anchor'].update(head=None,challenge=None)
    if fault=='nonce':q['anchor']['challenge']['nonce']='b'*64
    if fault=='trust':q['anchor']['trust']['max_head_age_ms']+=1
    if fault=='retention':q['anchor']['head']['retention_until_ms']=301200;sign(q['anchor']['head'])
    if fault=='before_checkpoint':q['anchor']['receipts'][-1]['retained_utc_ms']=q['checkpoints'][-1]['event']['utc_ms']-1;resign(q)
    if fault=='future_receipt':q['anchor']['receipts'][-1]['retained_utc_ms']=q['anchor']['now_utc_ms']+1;q['anchor']['head']['observed_utc_ms']+=1;resign(q)
    if fault=='start_prefix':q['anchor']['receipts'][0]['retained_utc_ms']=2;resign(q)
    if fault=='checkpoint_state':q['checkpoints'][0]['checkpoint']['cash']='1'
    if fault=='order':q['checkpoints'].reverse()
    if fault=='skip':q['checkpoints'].pop(0)
    if fault=='extra_field':q['expected_subjects']=[]
    assert verify(q)['binding_status']=='INVALID',fault


@pytest.mark.parametrize('bad',[None,{},[],True])
def test_malformed(bad):assert verify(bad)['binding_status']=='INVALID'


def test_empty_and_oversized(tmp_path):
    q=fixture(tmp_path,1);q['checkpoints']*=65
    assert verify(q)['binding_status']=='INVALID'
    q['checkpoints']=[];assert verify(q)['binding_status']=='INVALID'


def rehash_bundle(b):
    m,k,e,prev=b['manifest'],b['checkpoint'],b['event'],b['previous_event']
    e['session_id']=prev['session_id']=k['session_id']=H(m)
    k['previous_checkpoint_hash']=m['prior_checkpoint_hash']
    b['diagnostic_commit']['event_hash']=H(prev)
    e['previous_event_hash']=H(prev);k['journal_head_hash']=H(e)
    for field,obj in [('checkpoint_hash',k),('event_hash',e),('diagnostic_commit_hash',b['diagnostic_commit'])]:
        b['checkpoint_commit'][field]=H(obj)


@pytest.mark.parametrize('fault',['prior_anchor','prior_checkpoint','engine','plan','params','strategy','manifest_before_anchor','receipt_sequence'])
def test_resigned_locally_valid_chain_substitution(tmp_path,fault):
    from app.research.phase6_checkpoint_validation import verify_checkpoint
    q=fixture(tmp_path);b=q['checkpoints'][-1];m=b['manifest']
    if fault in ('prior_anchor','prior_checkpoint'):m[fault+'_hash']='f'*64
    if fault=='engine':m['engine_code_hash']='f'*64
    if fault=='plan':m['plan_hash']='f'*64
    if fault=='strategy':m['strategy_code_hash']='f'*64
    if fault=='params':
        m['params_hash']='f'*64;b['strategy_state']['params_hash']='f'*64
        h=H(b['strategy_state']);b['checkpoint']['strategy_state_hash']=b['payload']['strategy_state_hash']=b['checkpoint_commit']['strategy_state_hash']=b['diagnostic_commit']['output_state_hash']=h
        b['event']['payload_hash']=H(b['payload'])
    if fault=='manifest_before_anchor':m['persisted_utc_ms']=q['anchor']['receipts'][2]['retained_utc_ms']-1
    if fault=='receipt_sequence':
        # Checkpoint validator permits receipt sequence equal to segment2; make
        # prior receipt count also2, then resign both checkpoints consistently.
        first=q['checkpoints'][0];first['checkpoint']['last_receipt_seq']=first['payload']['last_receipt_seq']=2
        first['event']['payload_hash']=H(first['payload']);rehash_bundle(first)
        q['anchor']['receipts'][2]['subject_hash']=H(first['checkpoint']);resign(q)
        m['prior_checkpoint_hash']=H(first['checkpoint']);m['prior_anchor_hash']=H(q['anchor']['receipts'][2])
    rehash_bundle(b)
    assert verify_checkpoint(b)['binding_status']=='VALID'
    q['anchor']['receipts'][-1]['subject_hash']=H(b['checkpoint']);resign(q)
    assert verify(q)['binding_status']=='INVALID'


def test_snapshot_not_caller_mutation(tmp_path,monkeypatch):
    import app.research.phase6_checkpoint_anchor_validation as module
    q=fixture(tmp_path);expected=H(q['checkpoints'][-1]['checkpoint']);original=module.verify_anchors
    def mutate(request):
        result=original(request);q['checkpoints'].clear();q['anchor'].clear();return result
    monkeypatch.setattr(module,'verify_anchors',mutate)
    result=verify(q);assert result['binding_status']=='VALID' and result['checkpoint_hash']==expected


def intake_for(start,n):
    from test_phase6_intake_validation import fixture as source
    q=source();bar=next(iter(q['payloads'].values()));probe=next(iter(q['clock_probes'].values()));receipt=q['receipts'][0];segment=q['segments'][0]
    q.update(plan=copy.deepcopy(start['plan']),reservation=copy.deepcopy(start['reservation']),receipts=[],segments=[],payloads={},clock_probes={})
    run=H(q['reservation']);q['prefix'].update(run_id=run,segment_hashes=[])
    for i in range(1,n+1):
        close=60000+i*60000;t=close+10
        b=dict(bar,provider=q['plan']['provider'],instrument=q['plan']['instrument'],open_ms=close-60000,close_ms=close,close='100')
        c=dict(probe,clock_session_id='test',measured_utc_ms=close-1000,measured_monotonic_ns=(close-1000)*1000000,valid_until_monotonic_ns=(close+1000)*1000000)
        e=dict(receipt,run_id=run,receipt_seq=i,provider_event_ms=close,received_utc_ms=t,received_monotonic_ns=t*1000000,clock_session_id='test',clock_probe_hash=H(c),normalized_payload_hash=H(b),previous_receipt_hash=H(q['receipts'][-1]) if i>1 else '0'*64)
        seg=dict(segment,run_id=run,segment_seq=i,start_ms=close-60000,end_ms=close,bar_hash=H(b),accepted_receipt_hash=H(e),previous_segment_hash=H(q['segments'][-1]) if i>1 else '0'*64)
        q['payloads'][H(b)]=b;q['clock_probes'][H(c)]=c;q['receipts'].append(e);q['segments'].append(seg);q['prefix']['segment_hashes'].append(H(seg))
    return q
