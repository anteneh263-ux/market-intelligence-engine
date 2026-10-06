"""Pinned skfolio fold adapter; MIE owns normalization and interval purging."""
import hashlib
from importlib import metadata
from itertools import combinations
import math
from pathlib import Path
from .registry_inspection import _canonical, _hash, _hex
from .purge_embargo import purge_embargo
_PACKAGES = ('skfolio', 'numpy', 'scipy', 'pandas', 'cvxpy-base', 'clarabel', 'scikit-learn', 'joblib', 'plotly', 'cffi', 'qdldl', 'sparsediffpy', 'cloudpickle', 'python-dateutil', 'narwhals', 'packaging', 'six', 'threadpoolctl', 'pycparser')
_CHECKS = ('APPLICABILITY', 'ADAPTER_CONFIG', 'DEP_PINNING', 'FIXTURE_PARITY', 'SCHEMA_NORMALIZATION', 'ADAPTER_HASH')

def runtime_pin():
    """Read code/native-binary+METADATA hashes, not merely installed versions.

    Pin generation is an operator/build action, never automatic acceptance of
    a new runtime. Cooperative process boundary; not protection against a hostile
    interpreter or coordinated replacement of both installation and trusted pin.
    """
    packages = {}
    for name in _PACKAGES:
        dist = metadata.distribution(name)
        files = {}
        for item in dist.files or ():
            if str(item).endswith(('.py', '.so', '.dylib', 'METADATA')):
                path = Path(dist.locate_file(item))
                if path.is_symlink() or not path.is_file():
                    raise ValueError('DEP_PIN_MISMATCH')
                files[str(item)] = hashlib.sha256(path.read_bytes()).hexdigest()
        if not files:
            raise ValueError('DEP_PIN_MISMATCH')
        packages[name] = dict(version=dist.version, files_hash=_hash(files))
    return dict(name='skfolio', version=packages['skfolio']['version'], version_hash=_hash(packages))

def applicability_hash(request):
    return _hash({k: request[k] for k in ('experiment_id', 'cpcv_applicability', 'applicability_justification', 'folds_policy', 'pinned_lib')})

def _native(n, policy):
    k, t = (policy['n_folds'], policy['n_test_folds'])
    size = n // k
    blocks = [list(range(i * size, (i + 1) * size if i < k - 1 else n)) for i in range(k)]
    result = []
    for choice in combinations(range(k), t):
        test = sorted((j for i in choice for j in blocks[i]))
        excluded = set(test)
        for j in test:
            excluded.update(range(max(0, j - policy['purged_size']), j))
            excluded.update(range(j + 1, min(n, j + 1 + policy['purged_size'] + policy['embargo_size'])))
        result.append(dict(train_idx=[i for i in range(n) if i not in excluded], test_idx=test))
    return result

def _library(n, policy):
    import numpy as np
    from skfolio.model_selection import CombinatorialPurgedCV
    splitter = CombinatorialPurgedCV(**{k: policy[k] for k in ('n_folds', 'n_test_folds', 'purged_size', 'embargo_size')})
    return [dict(train_idx=[int(i) for i in train], test_idx=sorted((int(i) for block in tests for i in block))) for train, tests in splitter.split(np.zeros((n, 1)))]

def evaluate_cpcv(request, *, frozen_applicability_hash):
    report = dict(schema_version='wp5_4.adapter.v1', cpcv_applicability=None, folds=None, diagnostics=None, adapter_provenance=None, adapter_status='INVALID', checks=[dict(check_id=c, status='NOT_EVALUATED') for c in _CHECKS], reason_code=None, preregistration_binding='UPSTREAM_REQUIRED')
    index = 0
    try:
        import json
        q = json.loads(_canonical(request))
        _check(type(q) is dict and set(q) == {'experiment_id', 'cpcv_applicability', 'applicability_justification', 'sample_index', 'folds_policy', 'pinned_lib'})
        _check(_hex(q['experiment_id']) and _hex(frozen_applicability_hash))
        _check(q['cpcv_applicability'] in ('REQUIRED', 'OPTIONAL_USED', 'NOT_APPLICABLE'))
        _check(applicability_hash(q) == frozen_applicability_hash)
        report['cpcv_applicability'] = q['cpcv_applicability']
        if q['cpcv_applicability'] == 'NOT_APPLICABLE':
            _check(type(q['applicability_justification']) is str and q['applicability_justification'].strip())
            _check(q['folds_policy'] is None and q['pinned_lib'] is None and (q['sample_index'] == []))
            report.update(folds=[], diagnostics=dict(justification_hash=_hash(q['applicability_justification'])))
            for check in report['checks'][1:5]:
                check['status'] = 'PASS'
        else:
            report['checks'][0]['status'] = 'PASS'
            index = 1
            p = q['folds_policy']
            samples = q['sample_index']
            n = len(samples)
            _check(type(p) is dict and set(p) == {'n_folds', 'n_test_folds', 'purged_size', 'embargo_size', 'embargo_ms'})
            _check(all((type(v) is int for v in p.values())) and 3 <= p['n_folds'] <= n and (2 <= p['n_test_folds'] < p['n_folds']))
            _check(min(p['purged_size'], p['embargo_size'], p['embargo_ms']) >= 0)
            _check(math.comb(p['n_folds'], p['n_test_folds']) <= 10000)
            _check(p['purged_size'] + p['embargo_size'] < n // p['n_folds'] - 1)
            probe = purge_embargo(dict(sample_index=samples, test_window=[0, 1], embargo_ms=0, experiment_id=q['experiment_id']))
            _check(probe['purge_status'] == 'VALID')
            _check(all((a['feature_time_ms'] < b['feature_time_ms'] for a, b in zip(samples, samples[1:]))))
            report['checks'][1]['status'] = 'PASS'
            index = 2
            pin = runtime_pin()
            _check(q['pinned_lib'] == pin)
            report['adapter_provenance'] = pin
            report['checks'][2]['status'] = 'PASS'
            index = 3
            fixture = dict(n_folds=3, n_test_folds=2, purged_size=1, embargo_size=0, embargo_ms=0)
            expected = [dict(train_idx=[9, 10, 11], test_idx=list(range(8))), dict(train_idx=[5, 6], test_idx=[0, 1, 2, 3, 8, 9, 10, 11]), dict(train_idx=[0, 1, 2], test_idx=list(range(4, 12)))]
            _check(_library(12, fixture) == expected and _native(12, fixture) == expected)
            report['checks'][3]['status'] = 'PASS'
            index = 4
            folds = _library(n, p)
            _check(folds == _native(n, p))
            for fold in folds:
                retained = set(fold['train_idx'])
                for t in fold['test_idx']:
                    row = samples[t]
                    for j in list(retained):
                        train = samples[j]
                        overlap = train['label_start_ms'] < row['label_end_ms'] and row['label_start_ms'] < train['label_end_ms']
                        embargo = row['label_end_ms'] <= train['feature_time_ms'] < row['label_end_ms'] + p['embargo_ms']
                        if overlap or embargo:
                            retained.remove(j)
                fold['train_idx'] = sorted(retained)
                _check(retained and fold['test_idx'] and (not retained.intersection(fold['test_idx'])))
                fold['fold_id'] = _hash(dict(experiment_id=q['experiment_id'], sample_index_hash=_hash(samples), folds_policy=p, **fold))
            _check(runtime_pin() == pin)
            report.update(folds=folds, diagnostics=dict(n_splits=len(folds), input_hash=_hash(q)))
            report['checks'][4]['status'] = 'PASS'
        report['checks'][0]['status'] = 'PASS'
        report['checks'][5]['status'] = 'PASS'
        report['adapter_status'] = 'VALID'
    except (AssertionError, ValueError, TypeError, KeyError, ImportError, OSError, OverflowError, RecursionError):
        report.update(folds=None, diagnostics=None, adapter_provenance=None)
        report['checks'][index]['status'] = 'FAIL'
        report['reason_code'] = ('APPLICABILITY_INVALID', 'ADAPTER_CONFIG_INVALID', 'DEP_PIN_MISMATCH', 'FIXTURE_PARITY_MISMATCH', 'SCHEMA_NORMALIZATION_FAILED', 'ADAPTER_HASH_MISMATCH')[index]
    report['adapter_report_hash'] = _hash(report)
    return report

def _check(condition):
    if not condition:
        raise ValueError('CPCV_VALIDATION_FAILED')
