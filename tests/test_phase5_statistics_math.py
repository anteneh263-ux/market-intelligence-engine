import math
import pytest
from app.research.statistics_math import calculate_statistics


def test_symmetric_hand_oracle():
    # 30 alternating -1,+1: mean0, sample variance30/29, skew0, raw kurtosis1.
    r=calculate_statistics([-1.,1.]*15,n_total=2,sharpe_values=[0.,2.])
    assert r['psr']==0.5
    assert r['sr_observed']==0 and r['moments']=={'skew':0.0,'kurt_raw':1.0,'T':30}
    # N=2: first quantile=0; second Phi^-1(1-1/(2e))=0.9004525966377902.
    # gamma * quantile =0.5197553442805939; z=-benchmark*sqrt(29).
    assert r['sr_star0']==pytest.approx(0.5197553442805939,abs=1e-9)
    assert r['dsr']==pytest.approx(0.0025633093934803485,abs=1e-9)
    assert r['variance_convention']['ddof']==0 and r['var_sr_trials_recomputed']==1
    assert r['decision_input']['dsr_pass'] is False


def test_n_one_reports_psr_without_dsr():
    r=calculate_statistics([-1.,1.]*15,n_total=1,sharpe_values=[])
    assert r['psr']==.5 and r['dsr'] is None and r['sr_star0'] is None
    assert r['stats_status']=='INVALID' and r['reason_code']=='INSUFFICIENT_TRIALS_FOR_DSR'


@pytest.mark.parametrize('returns',[[],[1.]*30,[float('nan')]*30,[float('inf')]*30,[True]*30,[-1,1]*14,None])
def test_invalid_returns(returns):
    r=calculate_statistics(returns,n_total=2,sharpe_values=[0.,2.])
    assert r['stats_status']=='INVALID' and not r['decision_input']['dsr_pass']


@pytest.mark.parametrize('n',[0,-1,True,1.5])
def test_invalid_n(n):
    assert calculate_statistics([-1,1]*15,n_total=n,sharpe_values=[0.,2.])['reason_code']=='TRIAL_COUNTER_INVALID'


@pytest.mark.parametrize('values',[[1.,1.],[1.],[0.,1.,2.],[float('nan'),1.],[True,1.]])
def test_invalid_cross_trial_values(values):
    r=calculate_statistics([-1,1]*15,n_total=2,sharpe_values=values)
    assert r['stats_status']=='INVALID' and r['dsr'] is None


def test_failed_trials_still_count_in_deflation():
    a=calculate_statistics([-1,1]*15,n_total=2,sharpe_values=[0.,2.])
    b=calculate_statistics([-1,1]*15,n_total=5,sharpe_values=[0.,2.])
    assert b['K']==2 and b['N_total']==b['N_effective']==5
    assert b['sr_star0']>a['sr_star0'] and b['dsr']<a['dsr']


def test_scale_invariance_and_determinism():
    values=[-.2,.3,.1,-.1,.2]*10
    a=calculate_statistics(values,n_total=2,sharpe_values=[0.,1.])
    b=calculate_statistics([v*2 for v in values],n_total=2,sharpe_values=[0.,1.])
    assert a['dsr']==pytest.approx(b['dsr'],abs=1e-12)
    assert calculate_statistics(values,n_total=2,sharpe_values=[0.,1.])==a


def test_nonfinite_intermediate_refused():
    r=calculate_statistics([-1e308,1e308]*15,n_total=2,sharpe_values=[0.,1.])
    assert r['stats_status']=='INVALID'


def test_asymmetric_independent_decimal_oracle():
    # Independently evaluated with65-digit Decimal moments/erf/inverse bisection.
    r=calculate_statistics([-.2,.3,.1,-.1,.2]*10,n_total=2,sharpe_values=[0.,1.])
    assert r['sr_observed']==pytest.approx(.3202469976984697993,abs=1e-12)
    assert r['moments']['skew']==pytest.approx(-.1579875514375958184,abs=1e-12)
    assert r['moments']['kurt_raw']==pytest.approx(1.509194159004867496,abs=1e-12)
    assert r['psr']==pytest.approx(.9851331579803606534,abs=1e-12)
    assert r['sr_star0']==pytest.approx(.2598776721402969623,abs=1e-12)
    assert r['dsr']==pytest.approx(.6590039138026612679,abs=1e-12)
