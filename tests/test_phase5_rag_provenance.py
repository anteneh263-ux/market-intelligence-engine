import hashlib
import copy
import pytest
from app.research.rag_provenance import retrieve_advisory
from app.research.registry_inspection import _hash


def corpus():
    text='Transaction costs reduce historical strategy returns.'
    return [dict(source_id='synthetic-paper',content=text,content_hash=hashlib.sha256(text.encode()).hexdigest(),
                 claim_key='costs',stance='reduce')]


def request():return dict(query='transaction costs',phase='POST_FREEZE',max_items=2,action='RETRIEVE',requested_mutations=[])


def test_verified_retrieval_deterministic_and_read_only():
    sources=corpus();q=request();before=copy.deepcopy((sources,q));h=_hash(sources)
    r=retrieve_advisory(q,sources,expected_corpus_hash=h)
    assert r['rag_status']=='VALID_ADVISORY' and r['advisory'] is True
    assert r['retrieved'][0]['source_id']=='synthetic-paper'
    assert r==retrieve_advisory(q,sources,expected_corpus_hash=h)
    assert before==(sources,q)


def test_conflicts_detected_before_result_truncation():
    sources=corpus();other=dict(sources[0],source_id='other',stance='increase');sources.append(other)
    q=request();q['max_items']=1
    r=retrieve_advisory(q,sources,expected_corpus_hash=_hash(sources))
    assert r['rag_status']=='NEEDS_HUMAN_REVIEW' and r['conflicts']==['costs']


@pytest.mark.parametrize('change,reason',[(lambda q:q.update(requested_mutations=['dsr']),'RAG_WRITE_FORBIDDEN'),
    (lambda q:q.update(action='REINTERPRET_GATE'),'PHASE_BOUNDARY_VIOLATION'),
    (lambda q:q.update(phase='UNKNOWN'),'PHASE_BOUNDARY_VIOLATION')])
def test_no_write_or_gate_interpretation(change,reason):
    q=request();change(q);sources=corpus()
    r=retrieve_advisory(q,sources,expected_corpus_hash=_hash(sources))
    assert r['reason_code']==reason and r['retrieved']==[] and r['advisory'] is True


def test_source_hash_checked_even_when_corpus_hash_matches():
    sources=corpus();sources[0]['content']+=' Changed.'
    r=retrieve_advisory(request(),sources,expected_corpus_hash=_hash(sources))
    assert r['rag_status']=='INVALID' and r['reason_code']=='CONTENT_HASH_MISMATCH'


def test_empty_corpus_refuses():
    assert retrieve_advisory(request(),[],expected_corpus_hash=_hash([]))['rag_status']=='INVALID'
