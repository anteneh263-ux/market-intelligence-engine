import csv
import hashlib
import json
import pytest
from app.research.integration_pilot import run_integration_pilot, DAY
from app.research.trial_registry_v2 import execute_trial


def inputs(tmp):
    source=tmp/'bars.csv';start=1577836800000
    with source.open('w') as f:
        w=csv.writer(f);w.writerow(['timestamp','open','high','low','close','volume'])
        for i in range(100):
            c=100*(1.001**i)*(1.0003**(i%5));o=100*(1.001**max(0,i-1))*(1.0003**(max(0,i-1)%5))
            w.writerow([(start+i*DAY)//1000,o,max(c,o)+1,min(c,o)-1,c,1000])
    root=tmp/'evidence';root.mkdir()
    return dict(source_path=source,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        evidence_directory=root,owner='synthetic-test',boundaries=dict(
            train=dict(start_ms=start,end_ms=start+20*DAY),val=dict(start_ms=start+20*DAY,end_ms=start+80*DAY),
            holdout=dict(start_ms=start+80*DAY,end_ms=start+100*DAY)))


def test_two_preregistered_variants_real_path_and_no_holdout(tmp_path,monkeypatch):
    import app.research.integration_pilot as module
    kwargs=inputs(tmp_path);original=module.run_session_trial;seen=[]
    def checked(q,native,dataset,segment,**kw):
        plan=json.loads((kwargs['evidence_directory']/'PLAN.json').read_text())
        assert [t['params']['target'] for t in plan['trials']]==[.25,.75]
        assert q['attempt_fingerprint_record']['intended_dataset_zone']=='VAL'
        assert segment['end_ms']==kwargs['boundaries']['val']['end_ms']
        seen.append(kw['params']['target'])
        return original(q,native,dataset,segment,**kw)
    monkeypatch.setattr(module,'run_session_trial',checked)
    report=run_integration_pilot(**kwargs)
    assert report['engineering_status']=='VERIFIED' and report['N_total']==2 and seen==[.25,.75]
    assert not report['holdout_evaluated'] and not report['promotion_authorized'] and not report['prospective_verified']
    assert all(v['verification_status']=='VERIFIED' for v in report['verification'])
    assert all(s['N_total']==s['K']==2 for s in report['statistics'])
    assert (kwargs['evidence_directory']/'REPORT.json').exists()
    with pytest.raises(FileExistsError):run_integration_pilot(**kwargs)


def test_wrong_source_hash_before_evidence_write(tmp_path):
    kwargs=inputs(tmp_path);kwargs['source_sha256']='a'*64
    with pytest.raises(ValueError,match='SOURCE_HASH_MISMATCH'):run_integration_pilot(**kwargs)
    assert not list(kwargs['evidence_directory'].iterdir())


def test_source_changes_after_plan_are_counted_not_completed(tmp_path,monkeypatch):
    import app.research.integration_pilot as module
    kwargs=inputs(tmp_path);original=module.run_session_trial
    def corrupt(*args,**kw):
        kwargs['source_path'].write_text('changed')
        return original(*args,**kw)
    monkeypatch.setattr(module,'run_session_trial',corrupt)
    with pytest.raises(ValueError,match='SOURCE_HASH_MISMATCH'):run_integration_pilot(**kwargs)
    root=kwargs['evidence_directory'];plan=json.loads((root/'PLAN.json').read_text())
    program=plan['trials'][0]['request']['research_program_id']
    state=execute_trial(dict(mode='REGISTRY_STATE',research_program_id=program),str(root/'registry.sqlite'),str(root/'anchor.json'))
    assert state['N_total']==1 and state['result']['outcome_count']==0
    assert not (root/'REPORT.json').exists()


def test_statistical_stop_does_not_trigger_holdout(tmp_path,monkeypatch):
    import app.research.integration_pilot as module
    kwargs=inputs(tmp_path);original=module.evaluate_statistics_report
    def stop(*args,**kw):
        result=original(*args,**kw);result['decision_input']['dsr_pass']=False
        return result
    monkeypatch.setattr(module,'evaluate_statistics_report',stop)
    report=run_integration_pilot(**kwargs)
    assert report['statistical_gate']=='STOP' and not report['holdout_evaluated']
