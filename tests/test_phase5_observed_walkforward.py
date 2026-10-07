import copy
import json
from pathlib import Path
import pytest
from app.research.observed_walkforward import (run_observed_walkforward_trial,observed_wf_method_hash,
    observed_wf_selector_hash,verify_observed_walkforward_trial)
from app.research.phase5_identity_contract import validate_experiment_contract
from app.research.registry_inspection import _hash
from app.research.trial_registry_v2 import initialize_trial_registry,execute_trial
from test_phase5_contract_binding import request as identity_request
from test_phase5_observed_session import fixture

class Selector:
    def __init__(self,target=.25):self.target=target
    def select(self,features,labels):
        assert len(features)==len(labels)
        assert all(l['label_end_ms']>f['open_time'] for f,l in zip(features,labels))
        return {'target':self.target}

def setup(tmp,target=.25):
    args=fixture(closes=[100*(1.001**i)*(1.0002**(i%3)) for i in range(366)])
    rows=args['frame'].to_dict('records');d=86400000;t=rows[0]['open_time'];raw=identity_request('CONTINUOUS_24_7')
    coverage=dict(schema_version='wp5.session_coverage.v1',coverage_mode='CONTINUOUS_24_7',calendar_id=None,
        calendar_policy_hash=_hash(dict(market_hours=args['profile']['market_hours'],timeframe_ms=d)),open_times=[r['open_time'] for r in rows])
    data=dict(schema_version='wp5.session_dataset.v1',timeframe_ms=d,rows=[dict(open_time=r['open_time'],row_hash=_hash(r)) for r in rows],coverage=coverage)
    raw['dataset_identity']['dataset_hash']=_hash(data)
    policy=dict(schema_version='wp5.observed_wf_policy.v1',strategy_id=args['strategy'].strategy_id,
        asset='BTC/USD',timeframe='1d',initial_capital=100000.,min_train_rows=10,min_test_rows=31,
        embargo_ms=0,label_horizon_bars=1,warmup='NONE',reset='FRESH_CAPITAL_AND_STATE',baseline='FIRST_CLOSE',
        selection_rule='fixed-test-candidate',candidates=[{'target':.25},{'target':.75}],
        windows=[dict(window_id=i,train=[t+i*60*d,t+(i+1)*60*d],test=[t+(i+1)*60*d,t+(i+2)*60*d]) for i in range(2)])
    selector=Selector(target)
    kw={k:args[k] for k in ('profile','risk_limits','strategy')};kw['selector']=selector
    method=observed_wf_method_hash(policy,data,**kw)
    raw['experiment_configuration'].update(strategy_id=args['strategy'].strategy_id,method_hash=method,
        param_hash=observed_wf_selector_hash(policy,selector))
    contract=validate_experiment_contract(raw);assert contract['contract_status']=='VALID',contract
    q=dict(mode='RESERVATION',**{k:contract[k] for k in ('research_program_id','experiment_id','contract_hash')},contract_record=contract,
        attempt_fingerprint_record=dict(research_program_id=contract['research_program_id'],experiment_id=contract['experiment_id'],
        method_hash=method,ordered_input_hashes=[_hash(data),_hash(policy)],intended_dataset_zone='TRAIN'))
    out=tmp/'run';out.mkdir();reg=str(tmp/'registry');anchor=str(tmp/'anchor');initialize_trial_registry(reg,anchor)
    kw.update(evidence_directory=out,registry_path=reg,anchor_path=anchor,prior_result_artifacts={},
        read_bars=lambda a,b:copy.deepcopy([r for r in rows if a<=r['open_time']<b]))
    return q,policy,data,kw

def run(case):return run_observed_walkforward_trial(*case[:3],**case[3])

def test_reserves_before_training_and_freezes_before_each_oos(tmp_path):
    case=setup(tmp_path);q,p,data,kw=case;original=kw['read_bars'];seen=[]
    def guarded(a,b):
        state=execute_trial(dict(mode='REGISTRY_STATE',research_program_id=q['research_program_id']),kw['registry_path'],kw['anchor_path'])
        assert state['N_total']==1
        # Window 1 training reuses the previous OOS range; inspect invocation order.
        if len(seen)%2:
            window=len(seen)//2;root=kw['evidence_directory']/f'window-{window:04d}'
            assert (root/'PARAMETERS.json').exists() and (root/'OPEN_OOS.json').exists()
        seen.append((a,b));return original(a,b)
    kw['read_bars']=guarded;report=run(case)
    assert report['lifecycle_status']=='COMPLETED_VALID' and report['window_count']==2
    assert report['return_count']==118 and report['statistics']['statistics']['N_total']==1
    assert report['full_execution_timing_status']=='VERIFIED_FOR_EXECUTED_DECISIONS'
    assert report['prospective_verified'] is False and len(seen)==4

def test_policy_mutation_refuses_before_read_or_reservation(tmp_path):
    c=setup(tmp_path);c[1]['windows'][1]['test'][0]-=86400000
    c[3]['read_bars']=lambda a,b:pytest.fail('read before binding')
    with pytest.raises(ValueError):run(c)
    assert not list(c[3]['evidence_directory'].iterdir())

def test_changed_source_leaves_counted_reservation(tmp_path):
    c=setup(tmp_path);old=c[3]['read_bars']
    def changed(a,b):r=old(a,b);r[0]['close']+=1;return r
    c[3]['read_bars']=changed
    with pytest.raises(ValueError,match='DATASET_CONTENT_MISMATCH'):run(c)
    state=execute_trial(dict(mode='REGISTRY_STATE',research_program_id=c[0]['research_program_id']),c[3]['registry_path'],c[3]['anchor_path'])
    assert state['N_total']==1 and state['result']['outcome_count']==0

def test_readonly_replay_and_tampered_parameter_refusal(tmp_path):
    c=setup(tmp_path);result=run(c);root=c[3]['evidence_directory']
    before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    args={k:c[3][k] for k in ('registry_path','anchor_path')}
    verified=verify_observed_walkforward_trial(root,**args)
    assert verified['verification_status']=='VERIFIED' and not verified['counter_incremented']
    assert before=={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    path=root/'window-0000'/'PARAMETERS.json';params=json.loads(path.read_text());params['parameters']['target']=.75;path.write_text(json.dumps(params))
    with pytest.raises(ValueError):verify_observed_walkforward_trial(root,**args)

def rebind(case):
    q,p,data,kw=case;raw=identity_request('CONTINUOUS_24_7')
    raw['dataset_identity']['dataset_hash']=_hash(data)
    method=observed_wf_method_hash(p,data,**{k:kw[k] for k in ('profile','risk_limits','strategy','selector')})
    raw['experiment_configuration'].update(strategy_id=p['strategy_id'],method_hash=method,param_hash=observed_wf_selector_hash(p,kw['selector']))
    c=validate_experiment_contract(raw);assert c['contract_status']=='VALID'
    q.update({k:c[k] for k in ('research_program_id','experiment_id','contract_hash')});q['contract_record']=c
    q['attempt_fingerprint_record']=dict(research_program_id=c['research_program_id'],experiment_id=c['experiment_id'],method_hash=method,
        ordered_input_hashes=[_hash(data),_hash(p)],intended_dataset_zone='TRAIN')
    return case

class CausalResetSelector:
    def __init__(self):self.calls=0
    def select(self,features,labels):
        assert self.calls==0,'selector state carried across windows'
        self.calls+=1
        assert len(features)==59
        assert labels[-1]['label_end_ms']==features[-1]['open_time']+2*86400000
        assert all(label['sample_open_ms']==row['open_time'] for row,label in zip(features,labels))
        # Training feature 58 is the last retained row; row59 supplies its label.
        assert features[-1]['open_time']-features[0]['open_time']==58*86400000
        return {'target':.25}

def test_selector_labels_available_before_oos_and_initial_state_reset(tmp_path):
    c=setup(tmp_path);c[3]['selector']=CausalResetSelector();rebind(c);r=run(c)
    assert r['lifecycle_status']=='COMPLETED_VALID' and c[3]['selector'].calls==0
    root=c[3]['evidence_directory']
    for i,w in enumerate(c[1]['windows']):
        training=json.loads((root/f'window-{i:04d}'/'TRAINING.json').read_text())
        assert max(training['eligible_open_times'])+2*86400000<=w['train'][1]<=w['test'][0]

class UnknownCandidateSelector:
    def select(self,features,labels):return {'target':.99}

def test_unregistered_selection_refuses_before_oos_read(tmp_path):
    c=setup(tmp_path);c[3]['selector']=UnknownCandidateSelector();rebind(c)
    old=c[3]['read_bars'];calls=[]
    def counted(a,b):calls.append((a,b));return old(a,b)
    c[3]['read_bars']=counted
    with pytest.raises(ValueError,match='UNREGISTERED_PARAMETERS'):run(c)
    assert len(calls)==1 and not (c[3]['evidence_directory']/'window-0000'/'OPEN_OOS.json').exists()

@pytest.mark.parametrize('change',['delete','swap','mutate','extra'])
def test_window_evidence_changes_refuse(tmp_path,change):
    c=setup(tmp_path);run(c);root=c[3]['evidence_directory'];p=root/'window-0000'/'REPORT.json'
    if change=='delete':p.unlink()
    elif change=='swap':p.write_bytes((root/'window-0001'/'REPORT.json').read_bytes())
    elif change=='mutate':
        value=json.loads(p.read_text());value['returns'][0]+=.01;p.write_text(json.dumps(value))
    else:(root/'window-9999').mkdir()
    with pytest.raises((ValueError,OSError)):
        verify_observed_walkforward_trial(root,registry_path=c[3]['registry_path'],anchor_path=c[3]['anchor_path'])

def test_failed_oos_read_keeps_frozen_parameters_and_counted_reservation(tmp_path):
    c=setup(tmp_path);old=c[3]['read_bars'];count=0
    def interrupted(a,b):
        nonlocal count
        count+=1
        if count==2:raise OSError('source interrupted')
        return old(a,b)
    c[3]['read_bars']=interrupted
    with pytest.raises(OSError,match='source interrupted'):run(c)
    root=c[3]['evidence_directory']
    assert (root/'window-0000'/'PARAMETERS.json').exists() and not (root/'TRIAL_RESULT.json').exists()
    state=execute_trial(dict(mode='REGISTRY_STATE',research_program_id=c[0]['research_program_id']),c[3]['registry_path'],c[3]['anchor_path'])
    assert state['N_total']==1 and state['result']['outcome_count']==0

def test_risk_stopped_window_invalidates_complete_run_without_skipping(tmp_path):
    from app.paper.session.risk import freeze_risk_limits
    c=setup(tmp_path);old=c[3]['read_bars'];start=c[1]['windows'][0]['train'][0]
    # Build and commit different synthetic source before any evaluation.
    rows=[]
    for i in range(366):
        price=100.+i if i<90 else 1.
        rows.append(dict(open_time=start+i*86400000,open=price,high=price+1,low=max(.01,price-1),close=price,volume=1000.))
    c[2]['rows']=[dict(open_time=r['open_time'],row_hash=_hash(r)) for r in rows]
    c[3]['read_bars']=lambda a,b:copy.deepcopy([r for r in rows if a<=r['open_time']<b])
    c[3]['risk_limits']=freeze_risk_limits(max_drawdown=.05,max_session_loss=.05,max_daily_loss=.05,max_loss_per_trade=.05)
    rebind(c);result=run(c)
    assert result['lifecycle_status']=='COMPLETED_INVALID' and result['window_count']==1 and result['required_window_count']==2
    assert not (c[3]['evidence_directory']/'window-0001').exists() and result['statistics'] is None
    verified=verify_observed_walkforward_trial(c[3]['evidence_directory'],registry_path=c[3]['registry_path'],anchor_path=c[3]['anchor_path'])
    assert verified['verification_status']=='VERIFIED'

def test_two_whole_runs_count_as_two_trials_not_four_windows(tmp_path):
    a=tmp_path/'a';a.mkdir();b=tmp_path/'b';b.mkdir();one=setup(a,.25);first=run(one);two=setup(b,.75)
    for k in ('registry_path','anchor_path'):two[3][k]=one[3][k]
    artifact=json.loads((one[3]['evidence_directory']/'TRIAL_RESULT.json').read_text())
    two[3]['prior_result_artifacts']={_hash(artifact):artifact}
    assert one[0]['research_program_id']==two[0]['research_program_id']
    second=run(two);stats=second['statistics']
    assert stats['binding_status']=='VERIFIED' and stats['statistics']['N_total']==stats['statistics']['K']==2
    assert stats['statistics']['stats_status']=='VALID'
    verify_observed_walkforward_trial(one[3]['evidence_directory'],registry_path=one[3]['registry_path'],anchor_path=one[3]['anchor_path'])

def test_return_frequency_binds_window_schedule_and_differs_from_session(tmp_path):
    from app.research.observed_walkforward import _frequency
    c=setup(tmp_path);a=_frequency(c[1],86400000);p=copy.deepcopy(c[1]);p['windows'][1]['test'][1]+=86400000
    assert _hash(a)!=_hash(_frequency(p,86400000)) and a['frequency']=='WINDOW_BLOCK'
    assert a['baseline']=='FIRST_CLOSE' and a['reset']=='FRESH_CAPITAL_AND_STATE'

def test_wrong_committed_outcome_cannot_be_reported_valid(tmp_path,monkeypatch):
    import app.research.observed_walkforward as module
    c=setup(tmp_path);original=module.execute_trial
    def wrong(q,*a,**kw):
        if q['mode']=='OUTCOME_APPEND':q=dict(q,trial_outcome='COMPLETED_INVALID')
        return original(q,*a,**kw)
    monkeypatch.setattr(module,'execute_trial',wrong)
    with pytest.raises(ValueError,match='OUTCOME_BINDING_INVALID'):run(c)
    assert not (c[3]['evidence_directory']/'RESULT.json').exists()

def test_different_block_schedules_refuse_cross_trial_statistics(tmp_path):
    a=tmp_path/'a';a.mkdir();b=tmp_path/'b';b.mkdir();one=setup(a,.25);run(one);two=setup(b,.75)
    two[1]['windows'][-1]['test'][1]+=86400000;rebind(two)
    for k in ('registry_path','anchor_path'):two[3][k]=one[3][k]
    artifact=json.loads((one[3]['evidence_directory']/'TRIAL_RESULT.json').read_text())
    two[3]['prior_result_artifacts']={_hash(artifact):artifact}
    assert one[0]['research_program_id']==two[0]['research_program_id']
    result=run(two)
    assert result['lifecycle_status']=='COMPLETED_VALID'
    assert result['statistics']['binding_status']=='REFUSED' and result['statistics']['reason_code']=='TRIAL_VECTOR_BINDING_INVALID'
