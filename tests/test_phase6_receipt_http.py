import json
import pytest
from app.research.phase6_receipt_http import ReceiptHttpBoundary
from test_phase6_receipt_state import req

ARN='arn:aws:iam::123456789012:user/mie-operator'


def event(path='/append',payload=None):
    return dict(version='2.0',rawPath=path,rawQueryString='',isBase64Encoded=False,
        body=json.dumps(req() if payload is None else payload),requestContext=dict(accountId='123456789012',
        domainName='test.lambda-url.eu-north-1.on.aws',authorizer={'iam':{'userArn':ARN}},http=dict(method='POST',path=path)))


def boundary(callback):
    return ReceiptHttpBoundary(account_id='123456789012',domain_name='test.lambda-url.eu-north-1.on.aws',
        principal_arns=[ARN],append=callback,lookup=callback,head=callback)


@pytest.mark.parametrize('path,payload',[('/append',req()),('/lookup',{'request':req(),'nonce':'a'*64}),('/head',{'run_id':'a'*64,'nonce':'b'*64})])
def test_routes_pass_only_validated_payload(path,payload):
    seen=[]; h=boundary(lambda q: seen.append(q) or {'runtime_accepted':False})
    assert h.handle(event(path,payload))['statusCode']==200 and seen==[payload]


@pytest.mark.parametrize('fault',['principal','domain','owner','noiam'])
def test_unauthorized_never_dispatches(fault):
    seen=[]; h=boundary(lambda q:seen.append(q)); e=event(); c=e['requestContext']
    if fault=='principal': c['authorizer']['iam']['userArn']='arn:aws:iam::123456789012:root'
    if fault=='domain': c['domainName']='other'
    if fault=='owner': c['accountId']='999999999999'
    if fault=='noiam': c['authorizer']=None
    assert h.handle(e)['statusCode']==403 and not seen


@pytest.mark.parametrize('body',['{"x":1,"x":2}','{"x":NaN}','{"x":1.5}','[]','x'*16385])
def test_bad_body_no_dispatch(body):
    seen=[]; h=boundary(lambda q:seen.append(q)); e=event(); e['body']=body
    assert h.handle(e)['statusCode']==400 and not seen


def test_callback_failure_never_leaks_or_retries():
    calls=[]
    def fail(q): calls.append(q); raise RuntimeError('PRIVATE-SECRET')
    result=boundary(fail).handle(event())
    assert result['statusCode']==409 and 'PRIVATE' not in result['body']
    assert json.loads(result['body'])['retry_allowed'] is False and len(calls)==1
