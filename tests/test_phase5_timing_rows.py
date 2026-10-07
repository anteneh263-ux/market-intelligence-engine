import copy
import pytest
from app.research.timing_rows import check_timing_rows


def contract(mode='CONTINUOUS_24_7'):
    return dict(dataset_identity=dict(coverage_mode=mode,timeframe_ms=10,first_open_ms=0),
        boundaries={'train':dict(start_ms=0,end_ms=100)},
        derived_timing_offsets=dict(bar_available_offset_ms=10,signal_decision_offset_ms=10,
            order_eligibility_offset_ms=10,nominal_earliest_fill_offset_ms=10 if mode=='CONTINUOUS_24_7' else None,
            fill_bar_offset_lower_bound=1))


def row(opened=0):
    return dict(bar_open_ms=opened,bar_available_ms=opened+10,feature_observed_ms=opened+10,
                signal_decision_ms=opened+10,order_submitted_ms=opened+10,fill_ms=opened+10)


def check(rows,record=None):
    return check_timing_rows(rows,contract() if record is None else record,intended_dataset_zone='TRAIN')


def test_continuous_and_delayed_fill():
    rows=[row(),row(10)];rows[0]['fill_ms']=40;before=copy.deepcopy(rows)
    result=check(rows)
    assert result['status']=='PASS' and result['reason_code'] is None
    assert result['result']['timing_rows_coverage_status']=='RUNTIME_ORDER_FULLY_VERIFIED'
    assert result['result']['row_count']==2 and result['result']['last_bar_open_ms']==10
    assert check(rows)==result and rows==before


@pytest.mark.parametrize('mode',['SESSION_CALENDAR','SPARSE_DECLARED'])
def test_deferred_has_no_grid_claim(mode):
    r=row(3);r['fill_ms']=17
    result=check([r],contract(mode))
    assert result['status']=='PASS'
    assert result['result']['timing_rows_coverage_status']=='BAR_MEMBERSHIP_DEFERRED_TO_COVERAGE_VALIDATOR'


@pytest.mark.parametrize('rows',[[],None,{},[None],[{}]])
def test_bad_schema(rows):
    assert check(rows)['reason_code']=='TIMING_ROW_INVALID'


@pytest.mark.parametrize('field',list(row()))
@pytest.mark.parametrize('value',[None,True,1.5,'1'])
def test_integer_timestamps(field,value):
    r=row();r[field]=value
    assert check([r])['reason_code']=='TIMING_ROW_INVALID'


@pytest.mark.parametrize('field,value',[
 ('bar_open_ms',100),('bar_open_ms',1),('bar_available_ms',11),
 ('feature_observed_ms',9),('signal_decision_ms',9),('order_submitted_ms',9),
 ('fill_ms',9),('fill_ms',11)])
def test_continuous_predicates(field,value):
    r=row();r[field]=value
    assert check([r])['reason_code']=='TIMING_CONTRACT_MISMATCH'


def test_independent_offset_lower_bounds():
    for key,field in [('signal_decision_offset_ms','signal_decision_ms'),
                      ('order_eligibility_offset_ms','order_submitted_ms'),
                      ('nominal_earliest_fill_offset_ms','fill_ms')]:
        c=contract();c['derived_timing_offsets'][key]=20
        assert check([row()],c)['reason_code']=='TIMING_CONTRACT_MISMATCH'
    c=contract();c['derived_timing_offsets']['fill_bar_offset_lower_bound']=2
    assert check([row()],c)['reason_code']=='TIMING_CONTRACT_MISMATCH'


def test_duplicates_and_order():
    assert check([row(),row()])['reason_code']=='TIMING_ROW_DUPLICATE'
    assert check([row(10),row()])['reason_code']=='TIMING_ROW_OUT_OF_ORDER'


def test_unrecognized_contract_fails_closed():
    for c in ({},contract('UNKNOWN')):
        assert check([row()],c)['status']=='FAIL'


@pytest.mark.parametrize('mode',['SESSION_CALENDAR','SPARSE_DECLARED'])
@pytest.mark.parametrize('offset',['bar_available_offset_ms','signal_decision_offset_ms','order_eligibility_offset_ms'])
def test_deferred_required_offsets_cannot_be_null(mode,offset):
    c=contract(mode);c['derived_timing_offsets'][offset]=None
    assert check([row()],c)['reason_code']=='TIMING_CONTRACT_MISMATCH'


@pytest.mark.parametrize('mode',['CONTINUOUS_24_7','SESSION_CALENDAR','SPARSE_DECLARED'])
def test_actual_identity_contract_interface(mode):
    from test_phase5_contract_binding import request
    from app.research.phase5_identity_contract import validate_experiment_contract
    c=validate_experiment_contract(request(mode))
    assert c['contract_status']=='VALID'
    opened=c['dataset_identity']['first_open_ms'];duration=c['dataset_identity']['timeframe_ms']
    r=dict(bar_open_ms=opened,bar_available_ms=opened+duration,feature_observed_ms=opened+duration,
           signal_decision_ms=opened+duration,order_submitted_ms=opened+duration,fill_ms=opened+duration)
    assert check([r],c)['status']=='PASS'
