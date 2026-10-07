import copy
import hashlib
import json
import pytest
from app.research.purge_embargo import purge_embargo


def request():
    # Test [100,200), embargo [200,250): independent boundary truth table.
    cases=[('left',10,0,100),('right',300,200,300),('inside',150,120,180),
           ('straddle',50,50,150),('embargo_start',200,250,300),
           ('embargo_end',250,250,300),('both',200,150,220)]
    return dict(experiment_id='a'*64, test_window=[100,200], embargo_ms=50,
        sample_index=[dict(sample_id=i,feature_time_ms=f,label_start_ms=s,label_end_ms=e) for i,f,s,e in cases])


def test_hand_oracle_and_hash_binding():
    q=request();before=copy.deepcopy(q);result=purge_embargo(q)
    assert result['purge_status']=='VALID'
    assert result['purged_sample_ids']==['inside','straddle','both']
    assert result['embargoed_sample_ids']==['embargo_start']
    assert result['retained_sample_ids']==['left','right','embargo_end']
    assert result['purge_count']==3 and result['embargo_count']==1
    assert q==before and result==purge_embargo(q)
    body={'input':q,'report':{k:v for k,v in result.items() if k!='purge_report_hash'}}
    assert result['purge_report_hash']==hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    q['experiment_id']='b'*64
    assert purge_embargo(q)['purge_report_hash']!=result['purge_report_hash']


def test_zero_embargo():
    q=request();q['embargo_ms']=0;r=purge_embargo(q)
    assert r['embargoed_sample_ids']==[] and 'embargo_start' in r['retained_sample_ids']
    assert 'both' in r['purged_sample_ids']


@pytest.mark.parametrize('value',[None,True,{},'unknown',float('nan')])
def test_unknown_horizon(value):
    q=request();q['sample_index'][0]['label_end_ms']=value
    assert purge_embargo(q)['reason_code']=='INTERVAL_AMBIGUOUS'


@pytest.mark.parametrize('end',[0,-1])
def test_zero_or_reversed_interval(end):
    q=request();q['sample_index'][0]['label_end_ms']=end
    assert purge_embargo(q)['reason_code']=='INTERVAL_AMBIGUOUS'


@pytest.mark.parametrize('field',['sample_id','feature_time_ms','label_start_ms','label_end_ms'])
def test_missing_sample_field(field):
    q=request();del q['sample_index'][0][field]
    r=purge_embargo(q)
    assert r['reason_code']=='SAMPLE_SCHEMA_INVALID'
    assert all(r[k] is None for k in ('purged_sample_ids','embargoed_sample_ids','retained_sample_ids'))


@pytest.mark.parametrize('value',[None,True,-1,1.5])
def test_bad_embargo(value):
    q=request();q['embargo_ms']=value
    assert purge_embargo(q)['reason_code']=='EMBARGO_WINDOW_INVALID'


def test_duplicate_id():
    q=request();q['sample_index'].append(q['sample_index'][0].copy())
    assert purge_embargo(q)['reason_code']=='SAMPLE_SCHEMA_INVALID'


@pytest.mark.parametrize('value',[[100,100],[200,100],[True,200],[100,None]])
def test_invalid_test_interval(value):
    q=request();q['test_window']=value
    assert purge_embargo(q)['reason_code']=='INTERVAL_AMBIGUOUS'


def test_failure_order_and_no_partition():
    q=request();q['sample_index'][0]['label_end_ms']=None;q['embargo_ms']=-1
    r=purge_embargo(q)
    assert [x['status'] for x in r['checks']]==['PASS','FAIL','NOT_EVALUATED','NOT_EVALUATED','NOT_EVALUATED','NOT_EVALUATED']
    assert r['purge_report_hash'] is None


@pytest.mark.parametrize('q',[None,[],{},True])
def test_bad_top_level(q):
    assert purge_embargo(q)['reason_code']=='SAMPLE_SCHEMA_INVALID'


def test_empty_partition():
    q=request();q['sample_index']=[]
    r=purge_embargo(q)
    assert r['purge_status']=='VALID' and r['purge_count']==r['embargo_count']==0
