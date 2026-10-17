"""Export acknowledged journal bindings without arbitrary filesystem reads."""
from app.research.phase6_event_journal import EventJournal
from app.research.phase6_checkpoint_validation import verify_checkpoint
from app.research.phase6_validation import canonical_hash as H
from app.research.phase6_source_intake import need
from app.research.phase6_capacity import V2


def checkpoint_bundle(journal,manifest,result):
    need(type(journal) is EventJournal)
    status=journal.snapshot()
    need(status['lifecycle'] in ('CHECKPOINTED_LOCAL','CHECKPOINT_ANCHOR_BOUND_LOCAL'))
    read=journal.read_object
    c=read(result['commit_hash']);e=read(c['event_hash']);k=read(c['checkpoint_hash'])
    need(c['event_hash']==result['event_hash']==status['event_head_hash']
         and c['checkpoint_hash']==result['checkpoint_hash'] and H(manifest)==status['session_id'])
    bundle=dict(manifest=read(H(manifest)),checkpoint=k,checkpoint_commit=c,event=e,
        payload=read(e['payload_hash']),previous_event=read(e['previous_event_hash']),
        state=read(c['state_hash']),strategy_state=read(c['strategy_state_hash']),
        execution_commit=read(c['execution_commit_hash']),diagnostic_commit=read(c['diagnostic_commit_hash']))
    need(verify_checkpoint(bundle,capacity=V2)['binding_status']=='VALID')
    return bundle


def prior_receipt_proof(journal,bundle):
    """Read the exact predecessor invocation lineage before closing its writer."""
    need(type(journal) is EventJournal)
    read=journal.read_object
    d=read(bundle['diagnostic_commit']['invocation_hash']);ib=read(d['intake_binding_hash'])
    x=read(bundle['execution_commit']['invocation_hash'])
    proof=dict(diagnostic_invocation=d,intake_binding=ib,intake_bundle=read(ib['bundle_hash']),
        intake_validation=read(ib['validation_hash']),execution_invocation=x,
        quote_binding=None,quote_bundle=None,quote_validation=None)
    if 'quote_binding_hash' in x:
        qb=read(x['quote_binding_hash'])
        proof.update(quote_binding=qb,quote_bundle=read(qb['bundle_hash']),quote_validation=read(qb['validation_hash']))
    return proof
