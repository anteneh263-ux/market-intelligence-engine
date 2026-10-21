"""Verify advertised and actual matrix defaults without evaluation or corpus writes."""
import asyncio
import json
from types import SimpleNamespace
import pytest

from app.strategy_research.evaluation.frozen import DEFAULT_MATRIX, MATRIX_V1, MATRIX_V2, MATRIX_V4
from app.strategy_research import run


@pytest.mark.parametrize('argv,matrix', [([],MATRIX_V2),(['--matrix','v4'],MATRIX_V4)])
def test_standalone_catalogue_displays_selected_matrix(argv,matrix,capsys):
    assert run.main(argv)==0
    text=capsys.readouterr().out
    assert f'Matrix    : {matrix.version} {matrix.hash()}' in text


class Captured(Exception):
    pass


def test_standalone_evaluation_default_is_v2(monkeypatch):
    captured={}
    def evaluate(req,matrix,**kwargs):
        captured.update(request=req,matrix=matrix)
        raise Captured
    monkeypatch.setattr(run,'evaluate',evaluate)
    with pytest.raises(Captured):
        run.main(['--strategy','TEST-DEFAULT','--provider','synthetic','--assets','A'])
    assert captured['matrix'] is MATRIX_V2
    assert captured['request'].matrix_version==MATRIX_V2.version


def test_unified_cli_default_is_v2_with_writing_disabled(monkeypatch):
    from app import cli
    captured={}
    class FakeOrchestrator:
        def __init__(self,kb): pass
        def run_batch(self,*args,**kwargs):
            captured.update(kwargs)
            return []
    monkeypatch.setattr(cli,'Orchestrator',FakeOrchestrator)
    assert cli.main(['evaluate','--target','TEST-DEFAULT','--assets','A','--no-write'])==0
    assert captured['matrix_version']=='v2'
    assert captured['write'] is False


def test_core_default_and_latest_are_v2(monkeypatch,tmp_path):
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
            'TEST-DEFAULT',['A'],provider='synthetic',write=False)
    assert captured['matrix'] is MATRIX_V2
    assert captured['request'].matrix_version==MATRIX_V2.version
    assert versioning.resolve('latest') is MATRIX_V2
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


def test_platform_api_defaults_to_v2_but_writing_is_opted_out(monkeypatch):
    from fastapi import FastAPI
    from app.routers import platform as api
    captured={}
    class FakeOrchestrator:
        def __init__(self,kb): pass
        def run(self,*args,**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(matrix_version=MATRIX_V2.version)
    monkeypatch.setattr(api,'Orchestrator',FakeOrchestrator)
    app=FastAPI();app.include_router(api.router)
    response=post(app,'/api/engine/evaluate',
                  {'target':'TEST-DEFAULT','assets':['A'],'write':False})
    assert response['status']==200
    assert captured['matrix_version']=='v2'
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
