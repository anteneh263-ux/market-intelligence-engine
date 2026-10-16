import copy,json
from pathlib import Path
import pytest
from test_phase6_prior_state_binding import second,bind,execute
from app.research.phase6_validation import canonical_hash as H


def proof_from(tmp,prior):
    def read(h):return json.loads((tmp/'first'/(h+'.blob')).read_bytes())
    d=read(prior['diagnostic_commit']['invocation_hash']);i=read(d['intake_binding_hash']);x=read(prior['execution_commit']['invocation_hash'])
    q=dict(diagnostic_invocation=d,intake_binding=i,intake_bundle=read(i['bundle_hash']),intake_validation=read(i['validation_hash']),execution_invocation=x,quote_binding=None,quote_bundle=None,quote_validation=None)
    if x['schema_version']=='p6.quote_invocation.v2':
        b=read(x['quote_binding_hash']);q.update(quote_binding=b,quote_bundle=read(b['bundle_hash']),quote_validation=read(b['validation_hash']))
    return q


def setup(tmp,quote=False):
    j,w,q,pa,s,ss,p=second(tmp,quote=quote);bind(j,p,pa,s,ss)
    return j,w,q,pa,s,ss,p,proof_from(tmp,p)


@pytest.mark.parametrize('quote',[False,True])
def test_prior_receipts_bound_to_actual_next_callback(tmp_path,quote):
    j,w,q,pa,s,ss,p,proof=setup(tmp_path,quote);r=j.bind_prior_receipts(proof)
    from test_phase6_intake_binding import read
    result=read(w,r['validation_hash']);assert result['binding_status']=='VALID' and result['supplied_receipt_ancestry_verified'] and not result['runtime_accepted']
    d,out=execute(j,w,q,pa,s,ss,180010);inv=read(w,d['invocation_hash'])
    assert inv['schema_version']=='p6.diagnostic_invocation.v4' and inv['prior_receipt_binding_hash']==r['commit_hash'];j.close()


@pytest.mark.parametrize('field',['diagnostic_invocation','intake_binding','intake_bundle','intake_validation','execution_invocation','quote_binding','quote_bundle','quote_validation'])
def test_bad_proof_artifact_refused_before_writes(tmp_path,field):
    j,w,q,pa,s,ss,p,proof=setup(tmp_path,True);proof[field]['extra']=True;before=j.snapshot()
    with pytest.raises(ValueError):j.bind_prior_receipts(proof)
    assert j.snapshot()['lifecycle']=='INCOMPLETE' and j.snapshot()['objects']==before['objects']


@pytest.mark.parametrize('stage',[1,2,3])
def test_ancestry_publication_faults_terminal(tmp_path,monkeypatch,stage):
    j,w,q,pa,s,ss,p,proof=setup(tmp_path);put=w.put;count=0;before=j.snapshot()
    def fail(raw,**kw):
        nonlocal count
        h=put(raw,**kw);count+=1
        if count==stage:raise RuntimeError('uncertain write')
        return h
    monkeypatch.setattr(w,'put',fail)
    with pytest.raises(RuntimeError):j.bind_prior_receipts(proof)
    assert j.snapshot()['lifecycle']=='INCOMPLETE' and j.snapshot()['event_head_hash']==before['event_head_hash']


def test_one_shot_ancestry(tmp_path):
    j,w,q,pa,s,ss,p,proof=setup(tmp_path);j.bind_prior_receipts(proof)
    with pytest.raises(ValueError):j.bind_prior_receipts(proof)


def test_claimed_validation_not_trusted(tmp_path):
    j,w,q,pa,s,ss,p,proof=setup(tmp_path);proof['intake_validation']['runtime_accepted']=True
    proof['intake_binding']['validation_hash']=H(proof['intake_validation'])
    with pytest.raises(ValueError):j.bind_prior_receipts(proof)


def test_rehashed_current_quote_history_cannot_replace_prior_receipt(tmp_path):
    from app.research.phase6_prior_state_validation import verify_prior_state
    from app.research.phase6_prior_receipt_validation import verify_prior_receipts
    from test_phase6_intake_validation import rebind
    j,w,q,pa,s,ss,p,proof=setup(tmp_path,True);package=copy.deepcopy(j._prior_state_context['package'])
    package['intake']['receipts'][1]['raw_payload_hash']='b'*64;rebind(package['intake']);package['manifest']['segment_id']=H(package['intake']['segments'][-1])
    assert verify_prior_state(package)['binding_status']=='VALID'
    result=verify_prior_receipts(dict(prior_state=package,proof=proof))
    assert result['binding_status']=='INVALID' and result['reason_code']=='RECEIPT_ANCESTRY_INVALID';j.close()


def test_ancestry_root_swap_refused(tmp_path,monkeypatch):
    j,w,q,pa,s,ss,p,proof=setup(tmp_path);put=w.put;count=0;before=j.snapshot()
    def swap(raw,**kw):
        nonlocal count
        h=put(raw,**kw);count+=1
        if count==3:
            root=Path(w.root);root.rename(root.with_name('moved'));root.mkdir()
        return h
    monkeypatch.setattr(w,'put',swap)
    with pytest.raises(Exception):j.bind_prior_receipts(proof)
    assert j.snapshot()['lifecycle']=='INCOMPLETE' and j.snapshot()['event_head_hash']==before['event_head_hash']


def test_ancestry_reentry_refused(tmp_path,monkeypatch):
    j,w,q,pa,s,ss,p,proof=setup(tmp_path);put=w.put
    def reenter(raw,**kw):
        try:j.bind_prior_receipts(proof)
        except ValueError:pass
        return put(raw,**kw)
    monkeypatch.setattr(w,'put',reenter)
    with pytest.raises(Exception):j.bind_prior_receipts(proof)
    assert j.snapshot()['lifecycle']=='INCOMPLETE'


def test_foreign_ancestry_does_not_poison(tmp_path):
    import threading
    j,w,q,pa,s,ss,p,proof=setup(tmp_path);errors=[]
    def attempt():
        try:j.bind_prior_receipts(proof)
        except ValueError as e:errors.append(str(e))
    t=threading.Thread(target=attempt);t.start();t.join();assert errors and j.snapshot()['lifecycle']=='OPEN';j.bind_prior_receipts(proof);j.close()


@pytest.mark.parametrize('proof',[None,{},[],True])
def test_bad_ancestry_proof(tmp_path,proof):
    j,w,q,pa,s,ss,p,_=setup(tmp_path)
    with pytest.raises(ValueError):j.bind_prior_receipts(proof)


def test_ancestry_after_diagnostic_refused(tmp_path):
    j,w,q,pa,s,ss,p,proof=setup(tmp_path);execute(j,w,q,pa,s,ss,180010)
    with pytest.raises(ValueError):j.bind_prior_receipts(proof)
