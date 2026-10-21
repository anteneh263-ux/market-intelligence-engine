"""Synthetic report semantics and compatibility; no real corpus writes."""
from dataclasses import replace
from types import SimpleNamespace
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.strategy_research.evaluation import pipeline
from app.strategy_research.evaluation.frozen import MATRIX_V1, MATRIX_V2, MATRIX_V3, MATRIX_V4, EvalRequest
from app.strategy_research.strategies.base import Strategy
from app.strategy_research import dossier


class Fixture(Strategy):
    strategy_id = 'TEST-UNCERTAINTY'
    name = 'Synthetic uncertainty fixture'
    strategy_class = 'test'
    def positions(self, df, params=None):
        return pd.Series(np.where((np.arange(len(df))//11)%2, -1., 1.), index=df.index)


class Source:
    is_real = False
    def get(self, req):
        t = np.arange(260)
        price = 100 + .02*t + np.sin(t/(7 if req.asset == 'A' else 13))
        df = pd.DataFrame({'open_time': t, 'close': price, 'open': price,
                           'high': price+1, 'low': price-1, 'volume': 1.})
        return SimpleNamespace(df=df, n=len(df))


def report(monkeypatch, matrix, assets=('A', 'B')):
    monkeypatch.setattr(pipeline, 'get_strategy', lambda _: Fixture())
    monkeypatch.setattr(pipeline, 'get_provider', lambda *args, **kwargs: Source())
    matrix = replace(matrix, cost_bps_levels=(5.,), slippage_bps_levels=(5.,),
                     random_seeds=3, bootstrap_B=50)
    req = EvalRequest('TEST-UNCERTAINTY', 'synthetic', list(assets), '1d',
                      limit=260, matrix_version=matrix.version)
    return pipeline.evaluate(req, matrix)


@pytest.mark.parametrize('assets', [('A',), ('A', 'B')])
def test_range_is_not_a_confidence_interval(monkeypatch, assets):
    rep = report(monkeypatch, MATRIX_V4, assets)
    h = rep.headline
    assert 'sharpe_ci95' not in h
    assert h['sharpe_asset_range'] == [min(rep.per_asset_oos_sharpe.values()),
                                       max(rep.per_asset_oos_sharpe.values())]
    assert h['sharpe_uncertainty']['status'] == 'not_estimated'
    assert h['sharpe_uncertainty']['interval'] is None


def test_legacy_range_is_not_rendered_as_ci(monkeypatch):
    rep = report(monkeypatch, MATRIX_V2)
    text = dossier.render_step5_markdown(rep)
    assert 'Sharpe 95% CI' not in text
    assert 'range across assets' in text


@pytest.mark.parametrize('matrix', [MATRIX_V2, MATRIX_V3, MATRIX_V4])
def test_per_asset_render_does_not_claim_95_percent(monkeypatch, matrix):
    rep = report(monkeypatch, matrix, ('A',))
    text = dossier.render_step5_markdown(rep)
    assert 'range across assets' in text and 'Not estimated' in text
    assert '95%' not in text


def test_empty_assets_are_not_zero_uncertainty(monkeypatch):
    rep = report(monkeypatch, MATRIX_V4, ())
    assert rep.headline['sharpe_asset_count'] == 0
    assert rep.headline['sharpe_asset_range'] is None
    assert rep.headline['sharpe_uncertainty'] == {
        'status': 'not_estimated', 'interval': None, 'confidence_level': None,
        'method': None, 'reason': 'No sampling uncertainty estimator is specified for the '
                                'equal-weight mean of per-asset Sharpe. Asset min/max is descriptive.'}
    assert 'Unavailable' in dossier.render_step5_markdown(rep)
    assert rep.verdict == 'Inconclusive'


def test_single_asset_range_does_not_imply_certainty(monkeypatch):
    rep = report(monkeypatch, MATRIX_V4, ('A',))
    assert rep.headline['sharpe_asset_count'] == 1
    lo, hi = rep.headline['sharpe_asset_range']
    assert lo == hi
    assert rep.headline['sharpe_uncertainty']['confidence_level'] is None
    assert rep.headline['sharpe_uncertainty']['method'] is None
    assert rep.headline['sharpe_unit'] == 'per_bar'


def test_v1_bootstrap_label_and_insufficient_data(monkeypatch):
    rep = report(monkeypatch, MATRIX_V1)
    text = dossier.render_step5_markdown(rep)
    assert 'nominal 95% IID-bootstrap interval' in text
    assert 'serial dependence' in text
    assert 'range across assets' not in text
    empty = report(monkeypatch, MATRIX_V1, ())
    text = dossier.render_step5_markdown(empty)
    assert 'Unavailable' in text and '95%' not in text


def test_v3_evaluation_unchanged_v4_changes_only_interval_semantics(monkeypatch):
    v3 = report(monkeypatch, MATRIX_V3).to_dict()
    baseline = json.loads((Path(__file__).parent/'fixtures/sharpe_v3_legacy_report.json').read_text())
    # Values are deterministic, with portable floating-point tolerance.
    def compare(actual, expected):
        if isinstance(expected, dict):
            assert actual.keys() == expected.keys()
            for key in expected: compare(actual[key], expected[key])
        elif isinstance(expected, list):
            assert len(actual) == len(expected)
            for a,b in zip(actual,expected): compare(a,b)
        elif isinstance(expected,float):
            assert actual == pytest.approx(expected,rel=1e-12,abs=1e-14)
        else: assert actual == expected
    compare(v3,baseline)
    v4 = report(monkeypatch, MATRIX_V4).to_dict()
    assert v3['condition_rows'] == v4['condition_rows']
    assert v3['verdict'] == v4['verdict']
    assert v3['headline']['random_timing'] == v4['headline']['random_timing']
    assert v4['headline']['pooling'] == 'per_asset'
    oldh = {k:v for k,v in v3['headline'].items() if k != 'sharpe_ci95'}
    newh = {k:v for k,v in v4['headline'].items()
            if k not in ('sharpe_asset_range','sharpe_asset_count','sharpe_uncertainty')}
    assert oldh == newh
    assert v3['headline']['sharpe_ci95'] == v4['headline']['sharpe_asset_range']


def test_v4_preserves_existing_hashes_and_explicit_resolution():
    from app.research_core import versioning
    from app.strategy_research import MATRICES
    assert [m.hash() for m in (MATRIX_V1,MATRIX_V2,MATRIX_V3)] == [
        'a740f3acbf6572d6','4c2aa61eccfd276c','3b838b31e151d7d1']
    assert MATRIX_V4.hash() not in [m.hash() for m in (MATRIX_V1,MATRIX_V2,MATRIX_V3)]
    assert versioning.resolve('v2') is versioning.resolve('srp-matrix-v2') is MATRIX_V2
    assert versioning.resolve('v4') is MATRICES['v4'] is MATRICES['srp-matrix-v4'] is MATRIX_V4
    assert MATRIX_V4.random_timing_method == MATRIX_V3.random_timing_method


@pytest.mark.parametrize('request_version,selected', [
    ('srp-matrix-v4',MATRIX_V1),('srp-matrix-v4',MATRIX_V2),('srp-matrix-v4',MATRIX_V3),
    ('srp-matrix-v1',MATRIX_V4),('srp-matrix-v2',MATRIX_V4),('srp-matrix-v3',MATRIX_V4)])
def test_v4_identity_mismatch_rejected_before_fetch(monkeypatch,request_version,selected):
    def forbidden(*args): raise AssertionError('loaded before identity validation')
    monkeypatch.setattr(pipeline,'get_strategy',forbidden)
    req=EvalRequest('TEST-UNCERTAINTY','synthetic',[],'1d',matrix_version=request_version)
    with pytest.raises(ValueError,match='request.matrix_version'):
        pipeline.evaluate(req,selected)


@pytest.mark.parametrize('choice', [None,'v4'])
def test_v4_persists_separate_semantics_without_touching_old_archives(monkeypatch,tmp_path,choice):
    from app.knowledge_base import KnowledgeBase
    from app.research_core import Orchestrator
    monkeypatch.setattr(pipeline,'get_strategy',lambda _:Fixture())
    monkeypatch.setattr(pipeline,'get_provider',lambda *args,**kwargs:Source())
    rdir=tmp_path/'results';rdir.mkdir()
    prior={}
    for matrix in (MATRIX_V1,MATRIX_V2,MATRIX_V3):
        rep=report(monkeypatch,matrix)
        path=Path(dossier.write_results(str(tmp_path),rep));prior[path]=path.read_bytes()
    out=Orchestrator(KnowledgeBase(root=str(tmp_path))).run(
        'TEST-UNCERTAINTY',['A','B'],provider='synthetic',matrix_version=choice,limit=260,write=True)
    archived=json.loads((rdir/'TEST-UNCERTAINTY.srp-matrix-v4.json').read_text())
    for key in ('sharpe_asset_range','sharpe_asset_count','sharpe_uncertainty'):
        assert archived[key] == out.report['headline'][key]
    assert 'sharpe_ci95' not in archived
    assert archived['is_real'] is False
    assert archived == json.loads((rdir/'TEST-UNCERTAINTY.json').read_text())
    for path,content in prior.items(): assert path.read_bytes()==content


def test_v4_cli_reports_explicit_uncertainty(monkeypatch,capsys):
    from app.strategy_research.run import main
    monkeypatch.setattr(pipeline,'get_strategy',lambda _:Fixture())
    monkeypatch.setattr(pipeline,'get_provider',lambda *args,**kwargs:Source())
    assert main(['--strategy','TEST-UNCERTAINTY','--provider','synthetic','--assets','A',
                 '--matrix','v4','--limit','260','--json'])==0
    text=capsys.readouterr().out
    assert 'not_estimated' in text and 'sharpe_asset_range' in text
    assert 'sharpe_ci95' not in text



def test_v4_dossier_generator_versions_the_narrower_timing_claim(monkeypatch,tmp_path):
    from app.research_core import dossier_generator as generator
    monkeypatch.setattr(generator,'get_strategy',lambda _:Fixture())
    monkeypatch.setattr(generator,'get_spec',lambda _: {})
    old_name,old=generator.render('TEST-UNCERTAINTY',MATRIX_V3)
    new_name,new=generator.render('TEST-UNCERTAINTY',MATRIX_V4)
    assert old_name==new_name
    assert 'dossier-gen-v1' in old
    assert 'dossier-gen-v2' in new
    assert 'structurally impossible' not in new
    assert 'does not validate signal causality' in new
    assert generator.input_hash('TEST-UNCERTAINTY',MATRIX_V3)!=generator.input_hash('TEST-UNCERTAINTY',MATRIX_V4)
    # A later render must not silently overwrite a stored legacy dossier.
    (tmp_path/old_name).write_text(old)
    outcome=generator.ensure_dossier('TEST-UNCERTAINTY',str(tmp_path),MATRIX_V4)
    assert outcome['written'] is False
    assert (tmp_path/old_name).read_text()==old
