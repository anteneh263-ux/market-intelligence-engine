"""Versioned reporting on the actual fill path; never a frictionless rerun."""
import math
from .registry_inspection import _hash
from .validation_plan import require, finite, snapshot, read_artifact
from .observed_session import _read_journal

COST_KEYS=('fees_paid','spread_cost','slippage_cost')


def validate_reporting_policy(policy,duration):
    w=policy['window_policy'];r=policy['regime_policy']
    require(type(w) is dict and set(w)=={'mode','start_ms','train_span_ms','test_span_ms','step_ms','n_windows'} and
        w['mode'] in ('rolling','expanding') and all(type(w[k]) is int for k in w if k!='mode') and
        1<=w['n_windows']<=10000 and w['train_span_ms']>0 and w['test_span_ms']>0 and
        w['step_ms']>=w['test_span_ms'] and all(w[k]%duration==0 for k in ('train_span_ms','test_span_ms','step_ms')),
        'WINDOW_POLICY_INVALID')
    windows=[]
    for i in range(w['n_windows']):
        train_start=w['start_ms']+(i*w['step_ms'] if w['mode']=='rolling' else 0)
        test_start=w['start_ms']+w['train_span_ms']+i*w['step_ms']
        windows.append(dict(window_id=i,train=[train_start,test_start],test=[test_start,test_start+w['test_span_ms']]))
    require(windows==policy['windows'],'WINDOW_POLICY_INVALID')
    require(type(r) is dict and set(r)=={'rule','down_threshold','up_threshold'} and r['rule']=='TRAIN_CLOSE_RETURN_V1' and
        finite(r['down_threshold']) and finite(r['up_threshold']) and -1<r['down_threshold']<r['up_threshold'], 'REGIME_POLICY_INVALID')


def regime_receipt(policy,window,endpoint_rows,index,duration):
    rows=snapshot(endpoint_rows)
    expected=[window['train'][0],window['train'][1]-duration]
    require(type(rows) is list and len(rows)==2 and [r['open_time'] for r in rows]==expected and
        all(t in index and _hash(r)==index[t] and finite(r['close']) and r['close']>0 for t,r in zip(expected,rows)), 'REGIME_SOURCE_INVALID')
    change=rows[1]['close']/rows[0]['close']-1;r=policy['regime_policy']
    require(finite(change),'REGIME_SOURCE_INVALID')
    # Boundaries belong to the directional classes; neutral is an open interval.
    tag='DOWN' if change<=r['down_threshold'] else 'UP' if change>=r['up_threshold'] else 'NEUTRAL'
    return dict(schema_version='wp5.training_regime.v1',window=window,policy_hash=_hash(r),
        endpoint_rows=rows,training_close_return=change,regime_tag=tag,available_ms=window['train'][1])


def _close(a,b):
    return finite(a) and finite(b) and math.isclose(a,b,rel_tol=1e-11,abs_tol=1e-8)


def _metrics(equities):
    require(equities and all(finite(v) and v>0 for v in equities),'COST_ACCOUNTING_INVALID')
    returns=[b/a-1 for a,b in zip(equities,equities[1:])]
    peak=equities[0];dd=0.
    for value in equities:
        peak=max(peak,value);dd=max(dd,1-value/peak)
    return dict(n_returns=len(returns),returns=returns,total_return=equities[-1]/equities[0]-1,
        pnl=equities[-1]-equities[0],max_drawdown=dd)


def execution_accounting(directory,observed):
    """Caller verifies observed session first; derive from its hash-anchored journal."""
    native=read_artifact(directory,'MANIFEST.json')['native_manifest']
    journal=_read_journal(directory,native['manifest_hash']);profile=native['execution_profile']
    require(journal[-1]['event_hash']==observed['journal_head'],'COST_ACCOUNTING_INVALID')
    cash=native['initial_capital'];position=0.;costs={k:0. for k in COST_KEYS};points=[]
    def check_snapshot(snap,equity):
        require(all(finite(snap[k]) and snap[k]>=0 and _close(snap[k],costs[k]) for k in COST_KEYS) and
            _close(snap['cash'],cash) and _close(snap['position_units'],position) and
            finite(snap['mark_price']) and snap['mark_price']>0 and
            _close(equity,cash+position*snap['mark_price']),'COST_ACCOUNTING_INVALID')
    for e in journal:
        p=e['payload']
        if e['event_type']=='order':
            require(all(finite(p[k]) for k in ('filled_units','fill_price','fee','spread_cost','slippage_cost')) and
                min(p[k] for k in ('fee','spread_cost','slippage_cost'))>=0,'COST_ACCOUNTING_INVALID')
            units=p['filled_units']
            if p['status'] in ('filled','partial'):
                require(units!=0 and p['fill_price']>0,'COST_ACCOUNTING_INVALID')
                factor=1+math.copysign((profile['half_spread_bps']+profile['slippage_bps'])/10000,units)
                require(factor>0,'COST_ACCOUNTING_INVALID');reference=p['fill_price']/factor
                require(profile['fee_model'] in ('bps_on_notional','per_share_commission'),'COST_ACCOUNTING_INVALID')
                fee=(abs(units)*p['fill_price']*profile['taker_fee_bps']/10000 if profile['fee_model']=='bps_on_notional'
                    else max(profile['min_commission'],abs(units)*profile['commission_per_unit']))
                require(_close(p['fee'],fee) and _close(p['spread_cost'],abs(units)*reference*profile['half_spread_bps']/10000) and
                    _close(p['slippage_cost'],abs(units)*reference*profile['slippage_bps']/10000),'COST_ACCOUNTING_INVALID')
            else:
                require(p['status'] in ('rejected','no_trade') and units==0 and p['fee']==p['spread_cost']==p['slippage_cost']==0,'COST_ACCOUNTING_INVALID')
            cash-=units*p['fill_price'];cash-=p['fee'];position+=units
            require(_close(cash,p['cash_after']) and _close(position,p['position_after']),'COST_ACCOUNTING_INVALID')
            for out,key in zip(COST_KEYS,('fee','spread_cost','slippage_cost')):costs[out]+=p[key]
        elif e['event_type']=='portfolio_snapshot':
            check_snapshot(p,p['equity'])
            points.append(dict(timestamp_ms=e['timestamp'],net_equity=p['equity'],cumulative_costs=dict(costs)))
        elif e['event_type']=='session_final':
            require(p['broker']==observed['native_result']['broker'],'COST_ACCOUNTING_INVALID')
            check_snapshot(p['broker'],p['final_equity'])
            point=dict(timestamp_ms=e['timestamp'],net_equity=p['final_equity'],cumulative_costs=dict(costs))
            require(points and point['timestamp_ms']>=points[-1]['timestamp_ms'],'COST_ACCOUNTING_INVALID')
            if point['timestamp_ms']==points[-1]['timestamp_ms']:points[-1]=point
            else:points.append(point)
    require([dict(timestamp_ms=p['timestamp_ms'],equity=p['net_equity']) for p in points]==observed['equity_points'],'COST_ACCOUNTING_INVALID')
    baseline=points[0]['net_equity'];initial=points[0]['cumulative_costs']
    for p in points:
        p['cost_increment']=math.fsum(p['cumulative_costs'][k]-initial[k] for k in COST_KEYS)
        require(p['cost_increment']>=0,'COST_ACCOUNTING_INVALID')
        p['gross_equity']=p['net_equity']+p['cost_increment']
    net=_metrics([p['net_equity'] for p in points]);gross=_metrics([p['gross_equity'] for p in points])
    require(net['returns']==observed['net_returns'],'COST_ACCOUNTING_INVALID')
    modeled={k:points[-1]['cumulative_costs'][k]-initial[k] for k in COST_KEYS}
    modeled.update(total=points[-1]['cost_increment'],return_drag=points[-1]['cost_increment']/baseline)
    require(_close(net['pnl'],gross['pnl']-modeled['total']) and
        _close(net['total_return'],gross['total_return']-modeled['return_drag']),'COST_ACCOUNTING_INVALID')
    return dict(schema_version='wp5.actual_fill_accounting.v1',basis='COST_ADDED_BACK_ON_ACTUAL_FILLS',
        baseline='FIRST_CLOSE',baseline_equity=baseline,baseline_costs=initial,points=points,
        gross_metrics=gross,net_metrics=net,modeled_costs=modeled,counterfactual_frictionless=False)


def window_reporting(policy,window,regime,accounting,complete,duration):
    return dict(schema_version='wp5.observed_window_reporting.v3',regime_receipt_hash=_hash(regime),
        regime_tag=regime['regime_tag'],accounting=accounting,gross_metrics=accounting['gross_metrics'],
        net_metrics=accounting['net_metrics'],modeled_costs=accounting['modeled_costs'],
        coverage=dict(expected_close_points=(window['test'][1]-window['test'][0])//duration,
            observed_close_points=len(accounting['points'])),window_status='OK' if complete else 'INVALID')


def _group(windows):
    require(bool(windows),'WF_REPORT_BINDING_INVALID');n=len(windows)
    out=dict(window_count=n,window_ids=[w['window_id'] for w in windows],
        weighting='ARITHMETIC_MEAN_OF_WINDOW_TOTALS',continuous_portfolio=False)
    for name in ('gross','net'):
        out[name]=dict(mean_total_return=math.fsum(w[name+'_metrics']['total_return'] for w in windows)/n,
            mean_pnl=math.fsum(w[name+'_metrics']['pnl'] for w in windows)/n,
            worst_window_drawdown=max(w[name+'_metrics']['max_drawdown'] for w in windows))
    out['modeled_costs']=dict(mean_total=math.fsum(w['modeled_costs']['total'] for w in windows)/n,
        mean_return_drag=math.fsum(w['modeled_costs']['return_drag'] for w in windows)/n)
    return out


def summarize_windows(policy,reports,complete):
    windows=[]
    for i,w in enumerate(policy['windows']):
        if i>=len(reports):
            windows.append(dict(window_id=i,train=w['train'],test=w['test'],window_status='NOT_EVALUATED'));continue
        r=reports[i];v=r['reporting']
        windows.append(dict(window_id=i,train=w['train'],test=w['test'],param_hash=r['selected_param_hash'],
            **{k:v[k] for k in ('gross_metrics','net_metrics','modeled_costs','regime_tag','coverage','window_status')}))
    aggregate=None
    if complete:
        aggregate=_group(windows)
        aggregate['by_regime']={tag:_group([w for w in windows if w['regime_tag']==tag]) for tag in sorted({w['regime_tag'] for w in windows})}
    checks=[dict(check_id=k,status='PASS') for k in ('WINDOW_POLICY','PARAM_FREEZE','LOOKAHEAD_GUARD','PURGE_EMBARGO','COST_APPLICATION')]
    checks.append(dict(check_id='EXECUTION_COMPLETENESS',status='PASS' if complete else 'FAIL'))
    checks.append(dict(check_id='WF_HASH',status='PASS' if complete else 'NOT_EVALUATED'))
    result=dict(schema_version='wp5.observed_walkforward_summary.v3',window_policy=policy['window_policy'],
        regime_policy=policy['regime_policy'],windows=windows,aggregate=aggregate,checks=checks,
        walkforward_status='VALID' if complete else 'INVALID',reason_code=None if complete else 'WINDOW_EXECUTION_INCOMPLETE',
        accounting_basis='COST_ADDED_BACK_ON_ACTUAL_FILLS',continuous_portfolio=False)
    result['walkforward_report_hash']=_hash(result);return result
