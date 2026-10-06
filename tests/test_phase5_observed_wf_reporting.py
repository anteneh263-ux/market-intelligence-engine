import copy
import json
from pathlib import Path
import pytest
from app.research.registry_inspection import _hash, _canonical
from app.research.observed_walkforward import verify_observed_walkforward_trial
from test_phase5_observed_walkforward import setup, run, rebind


def reporting_case(root,mode='rolling'):
    c=setup(root);p=c[1];d=c[2]['timeframe_ms'];start=p['windows'][0]['train'][0]
    p.update(schema_version='wp5.observed_wf_policy.v3',validation_plan_hash='a'*64,
        window_policy=dict(mode=mode,start_ms=start,train_span_ms=60*d,test_span_ms=60*d,step_ms=60*d,n_windows=2),
        regime_policy=dict(rule='TRAIN_CLOSE_RETURN_V1',down_threshold=-.01,up_threshold=.01))
    if mode=='expanding':p['windows'][1]['train'][0]=start
    return rebind(c)


@pytest.mark.parametrize('mode',['rolling','expanding'])
def test_actual_reporting_and_readonly_replay(tmp_path,mode):
    c=reporting_case(tmp_path,mode);reader=c[3]['read_bars'];calls=[]
    def guarded(a,b):
        if len(calls)%2:
            folder=c[3]['evidence_directory']/f'window-{len(calls)//2:04d}'
            assert (folder/'REGIME.json').exists() and (folder/'PARAMETERS.json').exists()
        calls.append((a,b));return reader(a,b)
    c[3]['read_bars']=guarded;r=run(c)
    assert r['schema_version']=='wp5.observed_wf_report.v3' and r['lifecycle_status']=='COMPLETED_VALID'
    summary=r['walkforward'];assert summary['walkforward_status']=='VALID'
    assert summary['aggregate']['window_count']==2 and summary['aggregate']['by_regime']['UP']['window_count']==2
    assert all(w['regime_tag']=='UP' for w in summary['windows'])
    for w in summary['windows']:
        assert w['gross_metrics']['total_return']-w['modeled_costs']['return_drag']==pytest.approx(w['net_metrics']['total_return'])
        assert w['modeled_costs']['total']>0 and w['window_status']=='OK'
    root=c[3]['evidence_directory'];before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert verify_observed_walkforward_trial(root,**{k:c[3][k] for k in ('registry_path','anchor_path')})['verification_status']=='VERIFIED'
    assert before=={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()} and len(calls)==4


def test_schedule_mismatch_refused_before_reservation(tmp_path):
    c=reporting_case(tmp_path);c[1]['window_policy']['step_ms']+=86400000;rebind(c)
    c[3]['read_bars']=lambda a,b:pytest.fail('opened source')
    with pytest.raises(ValueError,match='WINDOW_POLICY_INVALID'):run(c)
    assert not list(c[3]['evidence_directory'].iterdir())


def test_regime_receipt_tamper_refused(tmp_path):
    c=reporting_case(tmp_path);run(c);root=c[3]['evidence_directory']
    path=root/'window-0000'/'REGIME.json';v=json.loads(path.read_text());v['endpoint_rows'][-1]['close']*=2;path.write_bytes(_canonical(v))
    with pytest.raises(ValueError):verify_observed_walkforward_trial(root,**{k:c[3][k] for k in ('registry_path','anchor_path')})


def test_outer_summary_reseal_cannot_change_accounting(tmp_path):
    c=reporting_case(tmp_path);run(c);root=c[3]['evidence_directory'];path=root/'RESULT.json';r=json.loads(path.read_text())
    r['walkforward']['aggregate']['net']['mean_total_return']+=1
    r['report_hash']=_hash({k:v for k,v in r.items() if k!='report_hash'});path.write_bytes(_canonical(r))
    with pytest.raises(ValueError,match='WF_REPORT_BINDING_INVALID'):
        verify_observed_walkforward_trial(root,**{k:c[3][k] for k in ('registry_path','anchor_path')})


def test_risk_stop_preserves_partial_window_and_suppresses_aggregate(tmp_path):
    from app.paper.session.risk import freeze_risk_limits
    c=reporting_case(tmp_path);start=c[1]['windows'][0]['train'][0];rows=[]
    for i in range(366):
        price=100.+i if i<90 else 1.
        rows.append(dict(open_time=start+i*86400000,open=price,high=price+1,low=max(.01,price-1),close=price,volume=1000.))
    c[2]['rows']=[dict(open_time=r['open_time'],row_hash=_hash(r)) for r in rows]
    c[3]['read_bars']=lambda a,b:copy.deepcopy([r for r in rows if a<=r['open_time']<b])
    c[3]['risk_limits']=freeze_risk_limits(max_drawdown=.05,max_session_loss=.05,max_daily_loss=.05,max_loss_per_trade=.05)
    rebind(c);r=run(c);s=r['walkforward']
    assert r['lifecycle_status']=='COMPLETED_INVALID' and s['aggregate'] is None
    assert [w['window_status'] for w in s['windows']]==['INVALID','NOT_EVALUATED']
    assert s['windows'][0]['modeled_costs']['total']>0
    assert verify_observed_walkforward_trial(c[3]['evidence_directory'],**{k:c[3][k] for k in ('registry_path','anchor_path')})['verification_status']=='VERIFIED'


def test_regime_exact_boundaries_and_source_membership():
    from app.research.observed_wf_reporting import regime_receipt
    p={'regime_policy':dict(rule='TRAIN_CLOSE_RETURN_V1',down_threshold=-.5,up_threshold=.5)}
    w=dict(window_id=0,train=[0,2],test=[2,40])
    for close,tag in [(1.,'DOWN'),(2.,'NEUTRAL'),(3.,'UP')]:
        rows=[dict(open_time=0,close=2.),dict(open_time=1,close=close)];index={r['open_time']:_hash(r) for r in rows}
        assert regime_receipt(p,w,rows,index,1)['regime_tag']==tag
    rows[-1]['open_time']=2
    with pytest.raises(ValueError,match='REGIME_SOURCE_INVALID'):regime_receipt(p,w,rows,index,1)


def test_invalid_regime_thresholds_refused_before_data(tmp_path):
    c=reporting_case(tmp_path);c[1]['regime_policy']['down_threshold']=.02;rebind(c)
    c[3]['read_bars']=lambda a,b:pytest.fail('source opened')
    with pytest.raises(ValueError,match='REGIME_POLICY_INVALID'):run(c)


def test_equal_window_weighting_is_not_continuous_compounding():
    from app.research.observed_wf_reporting import _group
    a=dict(window_id=0,gross_metrics=dict(total_return=.2,pnl=20.,max_drawdown=.1),net_metrics=dict(total_return=.1,pnl=10.,max_drawdown=.15),modeled_costs=dict(total=10.,return_drag=.1))
    b=copy.deepcopy(a);b['window_id']=1;b['net_metrics'].update(total_return=-.2,pnl=-20.);b['gross_metrics'].update(total_return=-.1,pnl=-10.)
    s=_group([a,b]);assert s['net']['mean_total_return']==pytest.approx(-.05)
    assert s['gross']['mean_total_return']==pytest.approx(.05) and not s['continuous_portfolio']


def accounting_fixture(monkeypatch,terminal_same_time=False):
    from app.research import observed_wf_reporting as module
    profile=dict(fee_model='bps_on_notional',taker_fee_bps=10.,half_spread_bps=50.,slippage_bps=50.)
    native=dict(manifest_hash='a'*64,initial_capital=1000.,execution_profile=profile)
    def snap(cash,pos,mark,fee,spread,slip):
        return dict(cash=cash,position_units=pos,mark_price=mark,equity=cash+pos*mark,fees_paid=fee,spread_cost=spread,slippage_cost=slip)
    first=snap(1000.,0.,100.,0.,0.,0.)
    mid=snap(494.495,5.,102.,.505,2.5,2.5)
    last=snap(1008.7802,0.,104.,1.0198,5.1,5.1)
    journal=[dict(event_type='portfolio_snapshot',timestamp=1,payload=first),
        dict(event_type='order',timestamp=1,payload=dict(status='partial',filled_units=5.,fill_price=101.,fee=.505,spread_cost=2.5,slippage_cost=2.5,cash_after=494.495,position_after=5.)),
        dict(event_type='portfolio_snapshot',timestamp=2,payload=mid),
        dict(event_type='order',timestamp=2,payload=dict(status='filled',filled_units=-5.,fill_price=102.96,fee=.5148,spread_cost=2.6,slippage_cost=2.6,cash_after=1008.7802,position_after=0.))]
    end=2 if terminal_same_time else 3
    journal.append(dict(event_type='session_final',timestamp=end,event_hash='b'*64,payload=dict(broker=last,final_equity=last['equity'])))
    equities=[first['equity'],last['equity']] if terminal_same_time else [first['equity'],mid['equity'],last['equity']]
    obs=dict(journal_head='b'*64,native_result=dict(broker=last),equity_points=[dict(timestamp_ms=i+1,equity=e) for i,e in enumerate(equities)],net_returns=[b/a-1 for a,b in zip(equities,equities[1:])])
    monkeypatch.setattr(module,'read_artifact',lambda *a:dict(native_manifest=native))
    monkeypatch.setattr(module,'_read_journal',lambda *a:journal)
    return module,obs,journal


@pytest.mark.parametrize('same_time',[False,True])
def test_hand_calculated_actual_fill_accounting(monkeypatch,same_time):
    module,obs,journal=accounting_fixture(monkeypatch,same_time)
    r=module.execution_accounting('/synthetic',obs)
    assert r['gross_metrics']['total_return']==pytest.approx(.02)
    assert r['net_metrics']['total_return']==pytest.approx(.0087802)
    assert r['modeled_costs']['total']==pytest.approx(11.2198)
    assert r['gross_metrics']['n_returns']==(1 if same_time else 2)
    assert r['counterfactual_frictionless'] is False


@pytest.mark.parametrize('mutation',['cost','cash','nan','negative','terminal'])
def test_inconsistent_accounting_refuses(monkeypatch,mutation):
    module,obs,journal=accounting_fixture(monkeypatch)
    if mutation=='cost':journal[2]['payload']['fees_paid']+=1
    elif mutation=='cash':journal[1]['payload']['cash_after']+=1
    elif mutation=='nan':journal[1]['payload']['fee']=float('nan')
    elif mutation=='negative':journal[1]['payload']['fee']=-1.
    else:journal[-1]['payload']['broker']['slippage_cost']+=1
    with pytest.raises(ValueError,match='COST_ACCOUNTING_INVALID'):module.execution_accounting('/synthetic',obs)


def test_reporting_policy_flows_through_full_pipeline(tmp_path):
    from test_phase5_observed_pipeline import setup as pipeline_setup,bind,run as pipeline_run
    from app.research.observed_pipeline import verify_observed_pipeline
    c=pipeline_setup(tmp_path);p=c[1];d=c[2]['timeframe_ms'];start=p['windows'][0]['train'][0]
    p.update(schema_version='wp5.observed_wf_policy.v3',
        window_policy=dict(mode='rolling',start_ms=start,train_span_ms=60*d,test_span_ms=60*d,step_ms=60*d,n_windows=2),
        regime_policy=dict(rule='TRAIN_CLOSE_RETURN_V1',down_threshold=-.01,up_threshold=.01))
    bind(c);r=pipeline_run(c)
    assert r['pipeline_status']=='COMPLETED',r
    assert r['trial']['walkforward']['walkforward_status']=='VALID'
    assert verify_observed_pipeline(c[4]['evidence_directory'],expected_report_hash=r['report_hash'])['verification_status']=='VERIFIED'


def test_native_minimum_commission_is_reconciled(tmp_path):
    from app.paper.session.profiles import freeze_execution_profile
    c=reporting_case(tmp_path);base=dict(c[3]['profile'],fee_model='per_share_commission',commission_per_unit=.001,min_commission=5.)
    c[3]['profile']=freeze_execution_profile(base['instrument'],**{k:v for k,v in base.items() if k not in ('instrument','profile_version')})
    rebind(c);r=run(c)
    assert r['lifecycle_status']=='COMPLETED_VALID'
    window=r['walkforward']['windows'][0]
    assert window['modeled_costs']['fees_paid']>=5.
    assert window['gross_metrics']['pnl']-window['modeled_costs']['total']==pytest.approx(window['net_metrics']['pnl'])


@pytest.mark.parametrize('field,value',[
    ('window_count',99),('required_window_count',99),('return_count',1),('blocks',[]),
    ('full_execution_timing_status','NO_EXECUTIONS'),('continuous_portfolio',True),
    ('prospective_verified',True),('trial_id','f'*64),('promotion_authorized',True),
])
def test_resealed_execution_claims_are_rejected(tmp_path,field,value):
    c=reporting_case(tmp_path);run(c);root=c[3]['evidence_directory'];path=root/'RESULT.json';r=json.loads(path.read_text())
    r[field]=value;r['report_hash']=_hash({k:v for k,v in r.items() if k!='report_hash'});path.write_bytes(_canonical(r))
    with pytest.raises(ValueError,match='WF_REPORT_BINDING_INVALID'):
        verify_observed_walkforward_trial(root,**{k:c[3][k] for k in ('registry_path','anchor_path')})
