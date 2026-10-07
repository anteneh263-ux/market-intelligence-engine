"""Immutable simulated SINGLE_PENDING execution stress, no live capabilities."""
import hashlib
import json
import math
import re


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def validate_policy(p):
    fields={'schema_version','seed','execution_delay_bars','missed_fill_probability','decision_policy','safe_exit_policy','broker_error_policy'}
    if not (type(p) is dict and set(p)==fields and p['schema_version']=='paper.stress_policy.v1' and
        type(p['seed']) is str and re.fullmatch('[0-9a-f]{64}',p['seed']) and
        type(p['execution_delay_bars']) is int and 0<=p['execution_delay_bars']<=10000 and
        type(p['missed_fill_probability']) in (int,float) and math.isfinite(p['missed_fill_probability']) and 0<=p['missed_fill_probability']<=1 and
        p['decision_policy']=='SINGLE_PENDING' and p['safe_exit_policy']=='SAME_DELAY_AND_MISS_ONCE' and p['broker_error_policy']=='FAIL_CLOSED'):
        raise ValueError('STRESS_POLICY_INVALID')
    return p


def missed(p,decision,attempt):
    validate_policy(p)
    draw=digest(dict(seed=p['seed'],decision_id=decision,attempt_open_ms=attempt))
    numerator,denominator=float(p['missed_fill_probability']).as_integer_ratio()
    return int(draw,16)*denominator<numerator*2**256
