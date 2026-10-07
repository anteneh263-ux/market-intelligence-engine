"""Deterministic lexical retrieval from a pinned corpus; advisory, never a gate."""
import hashlib
import re

from .registry_inspection import _hash, _hex
from .validation_plan import snapshot,require


def retrieve_advisory(request, corpus, *, expected_corpus_hash):
    report=dict(schema_version='wp5_7.advisory.v1',retrieved=[],advisory=True,phase=None,
        rag_status='INVALID',reason_code=None,corpus_hash=None,conflicts=[])
    try:
        q=snapshot(request)
        require(type(q) is dict and set(q)=={'query','phase','max_items','action','requested_mutations'},
                'RAG_WRITE_FORBIDDEN')
        require(q['phase'] in ('PRE_FREEZE','POST_FREEZE'),'PHASE_BOUNDARY_VIOLATION')
        report['phase']=q['phase']
        require(q['action']=='RETRIEVE','PHASE_BOUNDARY_VIOLATION')
        require(q['requested_mutations']==[],'RAG_WRITE_FORBIDDEN')
        require(type(q['query']) is str and q['query'].strip() and type(q['max_items']) is int and
                1<=q['max_items']<=100,'PHASE_BOUNDARY_VIOLATION')
        sources=snapshot(corpus)
        require(type(sources) is list and sources,'SOURCE_IDENTITY_MISSING')
        require(_hex(expected_corpus_hash) and _hash(sources)==expected_corpus_hash,'CONTENT_HASH_MISMATCH')
        ids=set();verified=[]
        tokens=set(re.findall(r'\w+',q['query'].casefold()))
        require(tokens,'PHASE_BOUNDARY_VIOLATION')
        for source in sources:
            require(type(source) is dict and set(source)=={'source_id','content','content_hash','claim_key','stance'},
                    'SOURCE_IDENTITY_MISSING')
            require(type(source['source_id']) is str and source['source_id'] and source['source_id'] not in ids and
                    type(source['claim_key']) is str and type(source['stance']) is str and
                    type(source['content']) is str,'SOURCE_IDENTITY_MISSING')
            ids.add(source['source_id'])
            require(_hex(source['content_hash']) and hashlib.sha256(source['content'].encode('utf-8')).hexdigest()==source['content_hash'],
                    'CONTENT_HASH_MISMATCH')
            words=set(re.findall(r'\w+',source['content'].casefold()))
            score=len(tokens&words)/len(tokens)
            if score:
                verified.append(dict(source_id=source['source_id'],content_hash=source['content_hash'],
                    snippet=source['content'][:500],score=score,claim_key=source['claim_key'],stance=source['stance']))
        # Conflicts are declared corpus metadata, not an automated truth judgement.
        claims={}
        for source in verified:
            if source['claim_key']:claims.setdefault(source['claim_key'],set()).add(source['stance'])
        conflicts=sorted(k for k,stances in claims.items() if len(stances)>1)
        report.update(retrieved=sorted(verified,key=lambda s:(-s['score'],s['source_id']))[:q['max_items']],
            corpus_hash=expected_corpus_hash,conflicts=conflicts,
            rag_status='NEEDS_HUMAN_REVIEW' if conflicts else 'VALID_ADVISORY',
            reason_code='RETRIEVAL_CONFLICT' if conflicts else None)
    except (ValueError,TypeError,KeyError,OverflowError,RecursionError) as exc:
        reason=str(exc)
        report.update(retrieved=[],corpus_hash=None,conflicts=[],reason_code=reason if reason in {
            'RAG_WRITE_FORBIDDEN','PHASE_BOUNDARY_VIOLATION','SOURCE_IDENTITY_MISSING','CONTENT_HASH_MISMATCH'}
            else 'CONTENT_HASH_MISMATCH')
    report['provenance_manifest_hash']=_hash(report)
    return report
