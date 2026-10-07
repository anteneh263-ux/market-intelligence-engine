"""Versioned historical WF adapter; explicit run root, no prospective claims.

Callbacks are trusted application code. Prefix isolation controls arguments,
not arbitrary filesystem/network access performed by a callback.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

import pandas as pd

from .purge_embargo import purge_embargo
from app.strategy_research.evaluation.backtest import run_backtest

_VERSION = 'wp5_2.historical_close_to_close.v1'
_CHECKS = ('WINDOW_POLICY', 'PARAM_FREEZE', 'LOOKAHEAD_GUARD', 'PURGE_EMBARGO',
           'COST_APPLICATION', 'WF_HASH')
_FIELDS = set(('experiment_id dataset_id strategy_id start_ms end_ms train_span_ms '
    'test_span_ms step_ms mode min_train_rows min_test_rows embargo_ms cost_bps '
    'slippage_bps timeframe callback_version regime_policy_ref aggregation').split())
_BAR_FIELDS = set('sample_id open_time close_time close label_start_ms label_end_ms'.split())


class _Refused(ValueError):
    def __init__(self, reason, check, insufficient=False):
        super().__init__(reason)
        self.reason, self.check, self.insufficient = reason, check, insufficient


def _raw(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def _copy(obj):
    return json.loads(_raw(obj))


def _hash(obj):
    return hashlib.sha256(_raw(obj)).hexdigest()


def _need(ok, reason, check, insufficient=False):
    if not ok:
        raise _Refused(reason, check, insufficient)


def _policy(value):
    _need(type(value) is dict and set(value) in (_FIELDS, _FIELDS | {'validation_plan_hash'}), 'WINDOW_POLICY_INVALID', 0)
    if 'validation_plan_hash' in value:
        _need(type(value['validation_plan_hash']) is str and re.fullmatch('[0-9a-f]{64}',value['validation_plan_hash']), 'WINDOW_POLICY_INVALID', 0)
    for key in ('experiment_id', 'dataset_id', 'callback_version', 'regime_policy_ref'):
        _need(type(value[key]) is str and re.fullmatch('[0-9a-f]{64}', value[key]), 'WINDOW_POLICY_INVALID', 0)
    _need(type(value['strategy_id']) is str and value['strategy_id'].strip() and
          'H025' not in re.sub('[^A-Z0-9]', '', value['strategy_id'].upper()), 'WINDOW_POLICY_INVALID', 0)
    for key in ('start_ms', 'end_ms', 'train_span_ms', 'test_span_ms', 'step_ms',
                'min_train_rows', 'min_test_rows', 'embargo_ms'):
        _need(type(value[key]) is int, 'WINDOW_POLICY_INVALID', 0)
    for key in ('train_span_ms', 'test_span_ms', 'step_ms', 'min_train_rows', 'min_test_rows'):
        _need(value[key] > 0, 'WINDOW_POLICY_INVALID', 0)
    _need(value['embargo_ms'] >= 0 and value['step_ms'] >= value['test_span_ms'] and
          value['end_ms'] - value['start_ms'] >= value['train_span_ms'] + value['test_span_ms'] and
          value['mode'] in ('rolling', 'expanding') and value['aggregation'] == 'disjoint_oos' and
          value['timeframe'] in ('1h', '4h', '1d', '1w'), 'WINDOW_POLICY_INVALID', 0)
    for key in ('cost_bps', 'slippage_bps'):
        _need(type(value[key]) in (int, float) and math.isfinite(value[key]) and value[key] >= 0,
              'WINDOW_POLICY_INVALID', 0)
    return _copy(value)


def _windows(p):
    result = []
    end = p['start_ms'] + p['train_span_ms']
    while end + p['test_span_ms'] <= p['end_ms']:
        start = p['start_ms'] if p['mode'] == 'expanding' else end - p['train_span_ms']
        result.append(dict(window_id=len(result), train=[start, end], test=[end, end+p['test_span_ms']],
                           param_hash=None, gross_metrics=None, net_metrics=None, regime_tag=None,
                           coverage=None, runtime_ledger=None, window_status='NOT_EVALUATED'))
        end += p['step_ms']
    return result


def _bars(reader, bounds):
    rows = reader(*bounds)
    _need(type(rows) is list, 'LOOKAHEAD_DETECTED', 2)
    rows = _copy(rows)
    ids, last = set(), bounds[0]
    gap = False
    for row in rows:
        _need(type(row) is dict and set(row) == _BAR_FIELDS, 'LOOKAHEAD_DETECTED', 2)
        _need(type(row['sample_id']) is str and row['sample_id'] and row['sample_id'] not in ids,
              'LOOKAHEAD_DETECTED', 2)
        ids.add(row['sample_id'])
        _need(all(type(row[k]) is int for k in ('open_time','close_time','label_start_ms','label_end_ms')),
              'LOOKAHEAD_DETECTED', 2)
        _need(last <= row['open_time'] < row['close_time'] <= bounds[1] and
              row['close_time'] <= row['label_start_ms'] < row['label_end_ms'] and
              type(row['close']) in (int, float) and math.isfinite(row['close']) and row['close'] > 0,
              'LOOKAHEAD_DETECTED', 2)
        gap = gap or row['open_time'] != last
        last = row['close_time']
    _need(rows and not gap and last == bounds[1], 'LOOKAHEAD_DETECTED', 2, True)
    return rows


class _Store:
    def __init__(self, directory):
        path = Path(directory)
        if not path.is_absolute() or path.resolve() != path or path.is_symlink():
            raise ValueError('Explicit absolute nonsymlink evidence directory required')
        if 'H025' in re.sub('[^A-Z0-9]', '', str(path).upper()):
            raise ValueError('H025 excluded')
        self.fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if os.listdir(self.fd):
                raise FileExistsError('Evidence directory must be empty; resume is forbidden')
        except BaseException:
            os.close(self.fd)
            raise

    def read(self, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        with os.fdopen(fd, 'rb') as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                raise OSError('Evidence file is not regular')
            return file.read()

    def put(self, name, value):
        raw = _raw(value)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        with os.fdopen(fd, 'wb') as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        os.fsync(self.fd)
        if self.read(name) != raw:
            raise OSError('Evidence readback mismatch')
        return raw

    def check(self, name, expected):
        try:
            same = self.read(name) == expected
        except OSError:
            same = False
        _need(same, 'PARAM_NOT_FROZEN', 1)

    def close(self):
        os.close(self.fd)


def _metrics(returns):
    # OOS-only, no annualization: caller timeframe is not verified bar duration.
    total = math.prod(1 + r for r in returns) - 1
    _need(math.isfinite(total), 'COST_MODEL_INVALID', 4)
    return dict(n_rows=len(returns), total_return=total,
                mean_return=math.fsum(returns)/len(returns), returns=returns)


def _features(rows):
    return [{k:r[k] for k in ('open_time','close_time','close')} for r in rows]


def run_walkforward(policy, *, evidence_directory, read_bars, select_parameters, signal, regime):
    """Execute synthetic/authorized historical WF under an explicit caller policy.

    Root claiming errors and report publication errors raise. Window failures
    return a sealed INVALID report, stop later windows, and omit aggregates.
    Dataset/strategy identity and trial registry binding remain upstream duties.
    """
    report = dict(schema_version=_VERSION, upstream_binding='NOT_PERFORMED', policy_hash=None,
                  windows=[], aggregate=None, walkforward_status='INVALID',
                  checks=[dict(check_id=c,status='NOT_EVALUATED') for c in _CHECKS], reason_code=None)
    def refuse(exc):
        report['reason_code'] = exc.reason
        report['checks'][exc.check]['status'] = 'FAIL'
        report['aggregate'] = None
    def seal():
        report['walkforward_report_hash'] = _hash(report)
        return report
    try:
        p = _policy(policy)
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        refuse(exc if isinstance(exc,_Refused) else _Refused('WINDOW_POLICY_INVALID',0))
        return seal()
    report['policy_hash'] = _hash(p)
    report['windows'] = _windows(p)
    report['checks'][0]['status'] = 'PASS'
    store = _Store(evidence_directory)
    try:
        store.put('RUN.json', dict(schema_version=_VERSION,policy=p))
        gross_all, net_all, times, regimes = [], [], [], {}
        for window in report['windows']:
            stage = 2
            try:
                training = _bars(read_bars,window['train'])
                stage = 3
                partition = purge_embargo(dict(sample_index=[dict(sample_id=r['sample_id'],
                    feature_time_ms=r['close_time'],label_start_ms=r['label_start_ms'],
                    label_end_ms=r['label_end_ms']) for r in training],
                    test_window=window['test'],embargo_ms=p['embargo_ms'],experiment_id=p['experiment_id']))
                _need(partition['purge_status']=='VALID','PURGE_EMBARGO_FAILED',3)
                retained = set(partition['retained_sample_ids'])
                eligible = [r for r in training if r['sample_id'] in retained and r['label_end_ms']<=window['train'][1]]
                window['coverage'] = dict(train_rows=len(training),eligible_train_rows=len(eligible),
                    purged_rows=partition['purge_count'],embargoed_rows=partition['embargo_count'],test_rows=None)
                _need(len(eligible)>=p['min_train_rows'],'PURGE_EMBARGO_FAILED',3,True)
                stage = 1
                params = _copy(select_parameters(_copy(eligible)))
                _need(type(params) is dict,'PARAM_NOT_FROZEN',1)
                frozen = dict(schema_version=_VERSION,policy_hash=report['policy_hash'],window_id=window['window_id'],
                    train=window['train'],test=window['test'],parameters=params,training_hash=_hash(training),
                    purge_report_hash=partition['purge_report_hash'])
                name = f"params-{window['window_id']}.json"
                raw = store.put(name,frozen)
                window['param_hash'] = _hash(params)
                if 'validation_plan_hash' in p:
                    window['frozen_artifact_hash'] = hashlib.sha256(raw).hexdigest()
                store.check(name,raw)
                store.put(f"open-{window['window_id']}.json",dict(event='OPEN_OOS',
                    frozen_artifact_hash=hashlib.sha256(raw).hexdigest(),test=window['test']))
                stage = 2
                testing = _bars(read_bars,window['test'])
                store.check(name,raw)
                _need(not set(r['sample_id'] for r in training).intersection(r['sample_id'] for r in testing),
                      'LOOKAHEAD_DETECTED',2)
                window['coverage']['test_rows'] = len(testing)
                _need(len(testing)>=p['min_test_rows'],'LOOKAHEAD_DETECTED',2,True)
                history = _features(training)
                tag = regime(_copy(history))
                store.check(name,raw)
                _need(type(tag) is str and bool(tag.strip()),'LOOKAHEAD_DETECTED',2)
                decisions, ledger = [], []
                for row in testing:
                    store.check(name,raw)
                    callback_params = _copy(params)
                    decision = signal(_copy(history),callback_params)
                    try:
                        unchanged = _raw(callback_params) == _raw(params)
                    except (ValueError, TypeError, RecursionError):
                        unchanged = False
                    _need(unchanged,'PARAM_NOT_FROZEN',1)
                    store.check(name,raw)
                    _need(type(decision) in (int,float) and decision in (-1,0,1),'LOOKAHEAD_DETECTED',2)
                    _need(history[-1]['close_time']<=row['open_time'],'LOOKAHEAD_DETECTED',2)
                    ledger.append(dict(max_available_ms=history[-1]['close_time'],
                        decision_time_ms=history[-1]['close_time'],priced_close_ms=row['close_time']))
                    decisions.append(decision)
                    history.extend(_features([row]))
                stage = 4
                frame = pd.DataFrame(_features([training[-1]]+testing))
                positions = pd.Series(decisions+[0.],index=frame.index)
                gross = run_backtest(frame,positions,p['timeframe'],0.,0.).net_returns[1:].tolist()
                net = run_backtest(frame,positions,p['timeframe'],p['cost_bps'],p['slippage_bps']).net_returns[1:].tolist()
                costs = [a-b for a,b in zip(gross,net)]
                _need(all(math.isfinite(v) for v in gross+net+costs) and all(c>=0 for c in costs) and
                      all(v > -1 for v in gross+net),
                      'COST_MODEL_INVALID',4)
                window.update(gross_metrics=_metrics(gross),net_metrics=_metrics(net),regime_tag=tag,
                    runtime_ledger=ledger,modeled_costs=costs,window_status='OK',test_data_hash=_hash(testing))
                if 'validation_plan_hash' in p:
                    window['decision_positions']=decisions+[0.]
                gross_all.extend(gross); net_all.extend(net); times.extend(r['close_time'] for r in testing)
                group = regimes.setdefault(tag,dict(gross=[],net=[]))
                group['gross'].extend(gross); group['net'].extend(net)
            except Exception as exc:
                failure = exc if isinstance(exc,_Refused) else _Refused(
                    {1:'PARAM_NOT_FROZEN',2:'LOOKAHEAD_DETECTED',3:'PURGE_EMBARGO_FAILED',4:'COST_MODEL_INVALID'}[stage],stage)
                window['window_status'] = 'INSUFFICIENT_DATA' if failure.insufficient else 'INVALID'
                refuse(failure)
                break
        else:
            report.update(walkforward_status='VALID',aggregate=dict(gross=_metrics(gross_all),net=_metrics(net_all),
                close_times_ms=times,by_regime={k:dict(n_rows=len(v['net']),gross=_metrics(v['gross']),net=_metrics(v['net']))
                    for k,v in sorted(regimes.items())}))
            for check in report['checks']:
                check['status']='PASS'
        seal()
        store.put('REPORT.json',report)
        return report
    finally:
        store.close()
