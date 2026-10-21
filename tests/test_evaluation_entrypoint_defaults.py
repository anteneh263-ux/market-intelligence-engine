"""Verify advertised and actual matrix defaults without evaluation or corpus writes."""
import asyncio
import json
from types import SimpleNamespace
import pytest

from app.strategy_research.evaluation.frozen import DEFAULT_MATRIX, MATRIX_V1, MATRIX_V2, MATRIX_V3, MATRIX_V4
from app.strategy_research import run


@pytest.mark.parametrize('argv,matrix', [([],MATRIX_V4),(['--matrix','v2'],MATRIX_V2)])
def test_standalone_catalogue_displays_selected_matrix(argv,matrix,capsys):
    assert run.main(argv)==0
    text=capsys.readouterr().out
    assert f'Matrix    : {matrix.version} {matrix.hash()}' in text


class Captured(Exception):
    pass


@pytest.mark.parametrize('choice,expected', [(None,MATRIX_V4),('v1',MATRIX_V1),('v2',MATRIX_V2),('v3',MATRIX_V3),('v4',MATRIX_V4)])
def test_standalone_evaluation_default_and_explicit_versions(monkeypatch,choice,expected):
    captured={}
    def evaluate(req,matrix,**kwargs):
        captured.update(request=req,matrix=matrix)
        raise Captured
    monkeypatch.setattr(run,'evaluate',evaluate)
    with pytest.raises(Captured):
        run.main(['--strategy','TEST-DEFAULT','--provider','synthetic','--assets','A']
                 + (['--matrix',choice] if choice else []))
    assert captured['matrix'] is expected
    assert captured['request'].matrix_version==expected.version


@pytest.mark.parametrize('command', ['evaluate','batch'])
@pytest.mark.parametrize('choice,expected', [(None,MATRIX_V4),('v1',MATRIX_V1),('v2',MATRIX_V2),('v3',MATRIX_V3),('v4',MATRIX_V4)])
def test_unified_evaluation_defaults_and_explicit_versions(monkeypatch,command,choice,expected):
    from app import cli
    captured={}
    class FakeOrchestrator:
        def __init__(self,kb): pass
        def run_batch(self,*args,**kwargs):
            captured.update(kwargs)
            return []
    monkeypatch.setattr(cli,'Orchestrator',FakeOrchestrator)
    argv=([command,'--target','TEST-DEFAULT','--no-write'] if command=='evaluate'
          else [command,'--targets','TEST-DEFAULT'])
    assert cli.main(argv+['--assets','A']+(['--matrix',choice] if choice else []))==0
    from app.research_core import versioning
    assert versioning.resolve(captured['matrix_version']) is expected
    assert captured['write'] is (command=='batch')  # intercepted; no writer runs


@pytest.mark.parametrize('choice,expected', [(None,MATRIX_V4),('latest',MATRIX_V4),('v1',MATRIX_V1),('v2',MATRIX_V2),('v3',MATRIX_V3),('v4',MATRIX_V4)])
def test_core_default_latest_and_explicit_versions(monkeypatch,tmp_path,choice,expected):
    from app.research_core import Orchestrator,module_registry,versioning
    from app.knowledge_base import KnowledgeBase
    captured={}
    def evaluate(req,matrix,**kwargs):
        captured.update(request=req,matrix=matrix)
        raise Captured
    module=SimpleNamespace(make_request=module_registry._strategy_request,evaluate=evaluate)
    monkeypatch.setattr(module_registry,'get_module',lambda _:module)
    with pytest.raises(Captured):
        Orchestrator(KnowledgeBase(root=str(tmp_path))).run(
            'TEST-DEFAULT',['A'],provider='synthetic',matrix_version=choice,write=False)
    assert captured['matrix'] is expected
    assert captured['request'].matrix_version==expected.version
    assert versioning.resolve('latest') is MATRIX_V4
    assert DEFAULT_MATRIX is MATRIX_V1


def test_legacy_api_defaults_to_v1_without_writing(monkeypatch):
    from fastapi import FastAPI
    from app.routers import strategy_research as api
    captured={}
    def evaluate(req,matrix,**kwargs):
        captured.update(request=req,matrix=matrix)
        return SimpleNamespace(to_dict=lambda:{'matrix_version':matrix.version})
    monkeypatch.setattr(api,'evaluate',evaluate)
    def forbidden(*args): raise AssertionError('unexpected KB write')
    monkeypatch.setattr(api,'apply_report',forbidden)
    app=FastAPI();app.include_router(api.router)
    response=post(app,'/api/strategy-research/evaluate',
                  {'strategy_id':'TEST-DEFAULT','assets':['A']})
    assert response['status']==200
    assert captured['matrix'] is MATRIX_V1
    assert captured['request'].matrix_version==MATRIX_V1.version


@pytest.mark.parametrize('choice,expected', [(None,MATRIX_V4),('v1',MATRIX_V1),('v2',MATRIX_V2),('v3',MATRIX_V3),('v4',MATRIX_V4)])
def test_platform_api_default_and_explicit_versions(monkeypatch,choice,expected):
    from fastapi import FastAPI
    from app.routers import platform as api
    captured={}
    class FakeOrchestrator:
        def __init__(self,kb): pass
        def run(self,*args,**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(matrix_version=expected.version)
    monkeypatch.setattr(api,'Orchestrator',FakeOrchestrator)
    app=FastAPI();app.include_router(api.router)
    response=post(app,'/api/engine/evaluate',
                  {'target':'TEST-DEFAULT','assets':['A'],'write':False,
                   **({'matrix':choice} if choice else {})})
    assert response['status']==200
    from app.research_core import versioning
    assert versioning.resolve(captured['matrix_version']) is expected
    assert captured['write'] is False



def post(app,path,payload):
    """Exercise the actual ASGI router/body defaults without sockets or httpx."""
    messages=[]
    body=json.dumps(payload).encode()
    async def invoke():
        sent=False
        async def receive():
            nonlocal sent
            if sent: return {'type':'http.disconnect'}
            sent=True
            return {'type':'http.request','body':body,'more_body':False}
        async def send(message): messages.append(message)
        await app({'type':'http','asgi':{'version':'3.0'},'http_version':'1.1',
                   'method':'POST','scheme':'http','path':path,'root_path':'',
                   'query_string':b'', 'headers':[(b'content-type',b'application/json')],
                   'client':('127.0.0.1',1234),'server':('testserver',80)},receive,send)
    asyncio.run(invoke())
    return next(m for m in messages if m['type']=='http.response.start')


@pytest.mark.parametrize('argv', [
    ['discover','--propose-only'], ['research','--pine-file','unused.pine'],
    ['safety','trials'], ['forward','list'],
])
def test_other_cli_workflows_keep_v2_without_running(monkeypatch,argv):
    import argparse
    from app import cli
    captured={}
    original=argparse.ArgumentParser.parse_args
    def parse(parser,*args,**kwargs):
        result=original(parser,*args,**kwargs)
        captured['matrix']=result.matrix
        raise Captured
    monkeypatch.setattr(argparse.ArgumentParser,'parse_args',parse)
    with pytest.raises(Captured):cli.main(argv)
    assert captured['matrix']=='v2'


def test_workflow_discovery_and_low_level_defaults_remain_legacy():
    import inspect
    from app.discovery.engine import StrategyDiscoveryEngine
    from app.research_core.workflow import research_strategy
    assert inspect.signature(StrategyDiscoveryEngine.run_round).parameters['matrix_version'].default=='v2'
    assert inspect.signature(research_strategy).parameters['matrix'].default=='v2'
    # Low-level request/evaluator compatibility stays v1.
    from app.strategy_research.evaluation import EvalRequest,evaluate
    assert EvalRequest('TEST-DEFAULT','synthetic',['A'],'1d').matrix_version==MATRIX_V1.version
    assert inspect.signature(evaluate).parameters['matrix'].default is MATRIX_V1


def test_matrix_catalogue_marks_only_v4_active():
    from app.research_core import versioning
    assert [m['version'] for m in versioning.list_versions() if m['active']]==[MATRIX_V4.version]
