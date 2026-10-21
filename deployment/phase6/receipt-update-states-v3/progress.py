"""One closed update-state contract; observation is never action authority."""
import time

VERSION = 'receipt-update-states.v3'
EVENT_SCHEMA = 'p6.qualification_operator_event.v3'
COMPLETE = 'UPDATE_COMPLETE'
TRANSIENT = frozenset({'UPDATE_IN_PROGRESS', 'UPDATE_COMPLETE_CLEANUP_IN_PROGRESS'})
OBSERVABLE = TRANSIENT | {COMPLETE}


def require(ok):
    if not ok:
        raise ValueError('UPDATE_STATE_V3_REFUSED')


def classify(state):
    require(type(state) is str and state in OBSERVABLE)
    return 'SETTLED' if state == COMPLETE else 'WAIT'


def wait_stack(call, health, *, stack_id, prefix, min_seconds, sleep=time.sleep):
    """Read only, 36 polls maximum; health checks the SAME frozen deadline each time.

    health(min_seconds, settled=False) must return a verified fresh observation of
    the pinned watch-only process/chain and continuous clock. A transient observation
    may be read, but return requires BOTH AWS and the observer to be fully settled.
    The caller must freshly bind identities/template before its next mutation.
    """
    require(type(min_seconds) is int and min_seconds > 0)
    require(type(prefix) is str and prefix and '/' not in prefix)
    for n in range(36):
        health(min_seconds, settled=False)
        response = call(prefix+'-'+str(n), 'cloudformation', 'describe-stacks', {'StackName':stack_id})
        require(not response.get('NextToken') and len(response['Stacks']) == 1)
        current = response['Stacks'][0]
        require(current['StackId'] == stack_id)
        state = classify(current['StackStatus'])
        observed = health(min_seconds, settled=False)
        latest = classify(observed['data']['stack_status'])
        if state == latest == 'SETTLED':
            return current
        sleep(5)
    raise TimeoutError('STACK_NOT_SETTLED_NO_FURTHER_ACTION')
